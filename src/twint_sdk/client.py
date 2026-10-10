from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any, Callable
from uuid import UUID, uuid4

import requests
import zeep
import zeep.exceptions

from . import __version__
from .certificate import Certificate, TempCertFile
from .environment import Environment, Version
from .exceptions import ApiFailure, CancellationFailed
from .values import (
    CustomerData,
    CustomerDataScope,
    FastCheckoutCheckIn,
    InteractiveFastCheckoutCheckIn,
    MerchantInformation,
    Money,
    Order,
    OrderRef,
    OrderStatus,
    PairingStatus,
    ShippingMethod,
    SystemStatus,
    TransactionStatus,
    to_order_reference,
)

SOFTWARE_NAME = "twint-express-checkout-sdk-python"

_POSTING_TYPE_GOODS = "GOODS"
_CASH_REGISTER_TYPE_EPOS = "EPOS"
_ORDER_KIND_PAYMENT_IMMEDIATE = "PAYMENT_IMMEDIATE"
_ORDER_KIND_REVERSAL = "REVERSAL"
_CANCELLATION_REASON_PAYMENT_ABORT = "PAYMENT_ABORT"
_PAYMENT_PAGE = "PAYMENT_PAGE"
_STATUS_TRANSITION_ERROR_CODE = "1200"

# Cash registers only need to be enrolled once per process, shared across client instances.
_enrolled_cash_registers: set[tuple[str, str, str]] = set()
_enrolled_lock = threading.Lock()


class TwintClient:
    """Client for the TWINT Merchant SOAP API.

    The express checkout flow is:

    1. :meth:`request_fast_checkout_check_in` - show the returned QR code (or redirect to the
       payment URL when ``hosted=True``).
    2. Poll :meth:`monitor_fast_checkout_check_in` until ``is_completed``; it then carries the
       customer data and chosen shipping method.
    3. Create your order and call :meth:`start_fast_checkout_order` with the final amount.
    4. Poll :meth:`monitor_order`; once ``needs_confirmation``, call :meth:`confirm_order`.
    """

    def __init__(
        self,
        certificate: Certificate,
        merchant: MerchantInformation,
        environment: Environment = Environment.TESTING,
        version: Version = Version.V10,
        *,
        timeout: float = 30,
        transport: zeep.Transport | None = None,
        message_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._merchant = merchant
        self._environment = environment
        self._message_id_factory = message_id_factory
        self._cert_file: TempCertFile | None = None

        if transport is None:
            self._cert_file = certificate.to_temp_file()
            session = requests.Session()
            session.cert = self._cert_file.path
            transport = zeep.Transport(session=session, timeout=timeout, operation_timeout=timeout)

        settings = zeep.Settings(strict=True, xml_huge_tree=False)
        self._zeep = zeep.Client(str(version.wsdl_path), transport=transport, settings=settings)
        self._service = self._zeep.create_service(
            f"{{{version.merchant_namespace}}}TWINTMerchantSOAPBinding",
            environment.soap_endpoint(version),
        )

    def close(self) -> None:
        if self._cert_file is not None:
            self._cert_file.close()

    def __enter__(self) -> TwintClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- System -----------------------------------------------------------------------------

    def check_system_status(self) -> SystemStatus:
        response = self._call(
            "CheckSystemStatus",
            MerchantInformation={"MerchantUuid": str(self._merchant.store_uuid), "CashRegisterId": ""},
        )
        return SystemStatus(response.Status)

    # --- Express checkout -------------------------------------------------------------------

    def request_fast_checkout_check_in(
        self,
        amount_without_shipping: Money,
        scopes: Iterable[CustomerDataScope],
        shipping_methods: Iterable[ShippingMethod] = (),
        *,
        hosted: bool = False,
    ) -> InteractiveFastCheckoutCheckIn:
        """Start an express checkout.

        With ``hosted=False`` the result carries a QR code (data URI) to render yourself; with
        ``hosted=True`` it carries a TWINT payment page URL to redirect the customer to.
        """
        self._enroll_cash_register()
        response = self._call(
            "RequestFastCheckoutCheckIn",
            MerchantInformation=self._merchant.to_soap(),
            NetAmount=amount_without_shipping.to_soap(),
            RequestedScopes=sorted(CustomerDataScope(s).value for s in scopes),
            ShippingMethods=[m.to_soap() for m in shipping_methods],
            **self._rendering(hosted),
        )
        return InteractiveFastCheckoutCheckIn(
            pairing_uuid=UUID(response.CheckInNotification.PairingUuid),
            pairing_status=PairingStatus(response.CheckInNotification.PairingStatus),
            pairing_token=response.Token.DisplayToken,
            qr_code=None if hosted else response.QRCode,
            payment_url=response.TwintURL if hosted else None,
        )

    def monitor_fast_checkout_check_in(self, pairing_uuid: UUID | str) -> FastCheckoutCheckIn:
        response = self._call(
            "MonitorFastCheckoutCheckIn",
            MerchantInformation=self._merchant.to_soap(),
            PairingUuid=str(pairing_uuid),
            WaitForResponse=False,
        )
        customer_data = None
        if response.CustomerData is not None:
            customer_data = CustomerData.from_fields({f.Name: f.Value for f in response.CustomerData.Field})
        return FastCheckoutCheckIn(
            pairing_uuid=UUID(str(pairing_uuid)),
            pairing_status=PairingStatus(response.CheckInNotification.PairingStatus),
            shipping_method_id=response.ShippingMethodId,
            customer_data=customer_data,
        )

    def cancel_fast_checkout_check_in(self, pairing_uuid: UUID | str) -> None:
        response = self._call(
            "CancelCheckIn",
            MerchantInformation=self._merchant.to_soap(),
            Reason=_CANCELLATION_REASON_PAYMENT_ABORT,
            PairingUuid=str(pairing_uuid),
        )
        if response.Status != "OK":
            raise CancellationFailed(f'Failed to cancel check-in, expected status "OK", got "{response.Status}"')

    def start_fast_checkout_order(
        self,
        pairing_uuid: UUID | str,
        merchant_transaction_reference: str,
        amount: Money,
        *,
        hosted: bool = False,
    ) -> Order:
        """Start the payment for a completed check-in. ``amount`` must include shipping."""
        self._enroll_cash_register()
        response = self._call(
            "StartOrder",
            MerchantInformation=self._merchant.to_soap(),
            Order=self._order_request(merchant_transaction_reference, amount),
            PairingUuid=str(pairing_uuid),
            **self._rendering(hosted),
        )
        return self._order_from_start_response(response, merchant_transaction_reference, amount)

    # --- Regular checkout -------------------------------------------------------------------

    def start_order(self, merchant_transaction_reference: str, amount: Money, *, hosted: bool = False) -> Order:
        self._enroll_cash_register()
        response = self._call(
            "StartOrder",
            MerchantInformation=self._merchant.to_soap(),
            Order=self._order_request(merchant_transaction_reference, amount),
            UnidentifiedCustomer=True,
            **self._rendering(hosted),
        )
        return self._order_from_start_response(response, merchant_transaction_reference, amount)

    # --- Order administration ---------------------------------------------------------------

    def monitor_order(self, order: OrderRef) -> Order:
        self._enroll_cash_register()
        response = self._call(
            "MonitorOrder",
            MerchantInformation=self._merchant.to_soap(),
            **to_order_reference(order).to_soap(),
            WaitForResponse=False,
        )
        return self._order_from_order_type(response.Order, PairingStatus(response.PairingStatus))

    def confirm_order(self, order: OrderRef, amount: Money) -> Order:
        self._enroll_cash_register()
        response = self._call(
            "ConfirmOrder",
            MerchantInformation=self._merchant.to_soap(),
            **to_order_reference(order).to_soap(),
            RequestedAmount=amount.to_soap(),
            PartialConfirmation=False,
        )
        return self._order_from_order_type(response.Order)

    def cancel_order(self, order: OrderRef) -> Order:
        self._enroll_cash_register()
        try:
            response = self._call(
                "CancelOrder",
                MerchantInformation=self._merchant.to_soap(),
                **to_order_reference(order).to_soap(),
            )
        except ApiFailure as e:
            if isinstance(e.__cause__, zeep.exceptions.Fault) and _is_status_transition_error(e.__cause__):
                raise CancellationFailed(str(e)) from e.__cause__
            raise
        return self._order_from_order_type(response.Order)

    def reverse_order(self, reversal_reference: str, order: OrderRef, amount: Money) -> Order:
        """Refund (part of) a successful order. ``reversal_reference`` must be a new, unique reference."""
        self._enroll_cash_register()
        order_request = self._order_request(reversal_reference, amount, kind=_ORDER_KIND_REVERSAL)
        order_request["Link"] = to_order_reference(order).to_soap()
        response = self._call(
            "StartOrder",
            MerchantInformation=self._merchant.to_soap(),
            Order=order_request,
            UnidentifiedCustomer=True,
        )
        return self._order_from_start_response(response, reversal_reference, amount)

    # --- Internals --------------------------------------------------------------------------

    def _call(self, operation: str, **kwargs: Any) -> Any:
        headers = {
            "requestHeader": {
                "MessageId": str(self._message_id_factory()),
                "ClientSoftwareName": SOFTWARE_NAME,
                "ClientSoftwareVersion": __version__,
            }
        }
        try:
            result = getattr(self._service, operation)(**kwargs, _soapheaders=headers)
        except zeep.exceptions.Fault as e:
            raise ApiFailure(f"{operation} failed: {e.message}") from e
        except (zeep.exceptions.Error, requests.RequestException, ValueError, TypeError) as e:
            raise ApiFailure(f"{operation} failed: {e}") from e
        # Operations with SOAP response headers return {"header": ..., "body": ...}.
        return result.body if hasattr(result, "body") and hasattr(result, "header") else result

    def _enroll_cash_register(self) -> None:
        key = (self._environment.value, str(self._merchant.store_uuid), self._merchant.cash_register_id)
        with _enrolled_lock:
            if key in _enrolled_cash_registers:
                return
        self._call(
            "EnrollCashRegister",
            MerchantInformation=self._merchant.to_soap(),
            CashRegisterType=_CASH_REGISTER_TYPE_EPOS,
        )
        with _enrolled_lock:
            _enrolled_cash_registers.add(key)

    @staticmethod
    def _rendering(hosted: bool) -> dict:
        return {"PaymentLayerRendering": _PAYMENT_PAGE} if hosted else {"QRCodeRendering": True}

    @staticmethod
    def _order_request(reference: str, amount: Money, kind: str = _ORDER_KIND_PAYMENT_IMMEDIATE) -> dict:
        return {
            "PostingType": _POSTING_TYPE_GOODS,
            "RequestedAmount": amount.to_soap(),
            "MerchantTransactionReference": reference,
            # XML attributes on OrderRequestType, not elements.
            "type": kind,
            "confirmationNeeded": kind == _ORDER_KIND_PAYMENT_IMMEDIATE,
        }

    @staticmethod
    def _order_from_start_response(response: Any, reference: str, amount: Money) -> Order:
        return Order(
            id=UUID(response.OrderUuid),
            merchant_transaction_reference=reference,
            status=OrderStatus(response.OrderStatus.Status._value_1),
            transaction_status=TransactionStatus(response.OrderStatus.Reason._value_1),
            amount=amount,
            pairing_status=PairingStatus(response.PairingStatus),
            pairing_token=str(response.Token) if response.Token is not None else None,
            qr_code=response.QRCode,
            payment_url=response.TwintURL,
        )

    @staticmethod
    def _order_from_order_type(order: Any, pairing_status: PairingStatus | None = None) -> Order:
        return Order(
            id=UUID(order.Uuid),
            merchant_transaction_reference=order.MerchantTransactionReference,
            status=OrderStatus(order.Status.Status._value_1),
            transaction_status=TransactionStatus(order.Status.Reason._value_1),
            amount=Money.from_soap(order.RequestedAmount),
            pairing_status=pairing_status,
        )


def _is_status_transition_error(fault: zeep.exceptions.Fault) -> bool:
    if fault.detail is None:
        return False
    for error_code in fault.detail.iter("{*}ErrorCode"):
        status = error_code.find("{*}Status")
        code = error_code.find("{*}Code")
        if (
            status is not None
            and code is not None
            and status.text == "STATUS_TRANSITION_ERROR"
            and code.text == _STATUS_TRANSITION_ERROR_CODE
        ):
            return True
    return False

