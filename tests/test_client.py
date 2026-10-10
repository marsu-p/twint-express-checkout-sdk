from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from twint_sdk import (
    Address,
    ApiFailure,
    CancellationFailed,
    CustomerDataScope,
    Money,
    OrderStatus,
    PairingStatus,
    ShippingMethod,
    SystemStatus,
    TransactionStatus,
)

from .conftest import MESSAGE_ID, NS, STORE_UUID, envelope, text

PAIRING_UUID = UUID("11111111-2222-4333-8444-555555555555")
ORDER_UUID = UUID("99999999-8888-4777-8666-555555555555")

MERCHANT_INFO = f"""<m:MerchantInformation>
  <b:MerchantUuid>{STORE_UUID}</b:MerchantUuid><b:CashRegisterId>webshop</b:CashRegisterId>
</m:MerchantInformation>"""


def order_status(status: str, reason: str) -> str:
    return f"""<m:Status code="1">{status}</m:Status><m:Reason code="2">{reason}</m:Reason>"""


def order_type(status: str, reason: str) -> str:
    return f"""<m:Order type="PAYMENT_IMMEDIATE" confirmationNeeded="true">
      <m:PostingType>GOODS</m:PostingType>
      <m:RequestedAmount><b:Amount>53.50</b:Amount><b:Currency>CHF</b:Currency></m:RequestedAmount>
      <m:MerchantTransactionReference>order-42</m:MerchantTransactionReference>
      <m:Uuid>{ORDER_UUID}</m:Uuid>
      <m:Status>{order_status(status, reason)}</m:Status>
      <m:CreationTimestamp>2026-10-10T10:00:00Z</m:CreationTimestamp>
      <m:PaymentAmount>
        <m:PaymentMethod>TWINT</m:PaymentMethod>
        <m:Amount><b:Amount>53.50</b:Amount><b:Currency>CHF</b:Currency></m:Amount>
      </m:PaymentAmount>
    </m:Order>"""


def test_check_system_status(client, transport):
    transport.add(
        "CheckSystemStatus", envelope("<m:CheckSystemStatusResponseElement><m:Status>OK</m:Status></m:CheckSystemStatusResponseElement>")
    )

    assert client.check_system_status() is SystemStatus.OK

    request = transport.last("CheckSystemStatus")
    assert text(request, ".//h:RequestHeaderElement/h:MessageId") == str(MESSAGE_ID)
    assert text(request, ".//h:RequestHeaderElement/h:ClientSoftwareName") == "twint-express-checkout-sdk-python"


def test_request_fast_checkout_check_in(client, transport):
    transport.add(
        "RequestFastCheckoutCheckIn",
        envelope(f"""<m:RequestFastCheckoutCheckInResponseElement>
          <m:CheckInNotification>{MERCHANT_INFO}
            <m:PairingUuid>{PAIRING_UUID}</m:PairingUuid>
            <m:PairingStatus>PAIRING_IN_PROGRESS</m:PairingStatus>
          </m:CheckInNotification>
          <m:Token><b:DisplayToken>AB12CD</b:DisplayToken></m:Token>
          <m:QRCode>data:image/png;base64,iVBORw0KGgo=</m:QRCode>
        </m:RequestFastCheckoutCheckInResponseElement>"""),
    )

    check_in = client.request_fast_checkout_check_in(
        Money.chf("49.90"),
        [CustomerDataScope.EMAIL, CustomerDataScope.SHIPPING_ADDRESS],
        [ShippingMethod("post-a", "A-Post", Money.chf("3.60"))],
    )

    assert check_in.pairing_uuid == PAIRING_UUID
    assert check_in.pairing_status is PairingStatus.PAIRING_IN_PROGRESS
    assert check_in.pairing_token == "AB12CD"
    assert check_in.qr_code == "data:image/png;base64,iVBORw0KGgo="
    assert check_in.payment_url is None

    assert transport.actions() == ["EnrollCashRegister", "RequestFastCheckoutCheckIn"]
    request = transport.last("RequestFastCheckoutCheckIn")
    assert text(request, ".//m:NetAmount/b:Amount") == "49.90"
    assert [e.text for e in request.findall(".//m:RequestedScopes", NS)] == ["email", "shipping_address"]
    assert text(request, ".//m:ShippingMethods/m:ShippingMethodId") == "post-a"
    assert text(request, ".//m:ShippingMethods/m:ShippingMethodAmount/b:Amount") == "3.60"
    assert text(request, ".//m:QRCodeRendering") == "true"


def test_hosted_fast_checkout_check_in_returns_payment_url(client, transport):
    transport.add(
        "RequestFastCheckoutCheckIn",
        envelope(f"""<m:RequestFastCheckoutCheckInResponseElement>
          <m:CheckInNotification>{MERCHANT_INFO}
            <m:PairingUuid>{PAIRING_UUID}</m:PairingUuid>
            <m:PairingStatus>PAIRING_IN_PROGRESS</m:PairingStatus>
          </m:CheckInNotification>
          <m:Token><b:DisplayToken>AB12CD</b:DisplayToken></m:Token>
          <m:TwintURL>https://pay.twint.ch/xyz</m:TwintURL>
        </m:RequestFastCheckoutCheckInResponseElement>"""),
    )

    check_in = client.request_fast_checkout_check_in(Money.chf(10), [CustomerDataScope.EMAIL], hosted=True)

    assert check_in.payment_url == "https://pay.twint.ch/xyz"
    assert check_in.qr_code is None
    assert text(transport.last("RequestFastCheckoutCheckIn"), ".//m:PaymentLayerRendering") == "PAYMENT_PAGE"


def test_monitor_fast_checkout_check_in_in_progress(client, transport):
    transport.add(
        "MonitorFastCheckoutCheckIn",
        envelope(f"""<m:MonitorFastCheckoutCheckInResponseElement>
          <m:CheckInNotification>{MERCHANT_INFO}
            <m:PairingUuid>{PAIRING_UUID}</m:PairingUuid>
            <m:PairingStatus>PAIRING_IN_PROGRESS</m:PairingStatus>
          </m:CheckInNotification>
        </m:MonitorFastCheckoutCheckInResponseElement>"""),
    )

    check_in = client.monitor_fast_checkout_check_in(PAIRING_UUID)

    assert not check_in.is_completed
    assert check_in.customer_data is None


def test_monitor_fast_checkout_check_in_completed(client, transport):
    transport.add(
        "MonitorFastCheckoutCheckIn",
        envelope(f"""<m:MonitorFastCheckoutCheckInResponseElement>
          <m:CheckInNotification>{MERCHANT_INFO}
            <m:PairingUuid>{PAIRING_UUID}</m:PairingUuid>
            <m:PairingStatus>PAIRING_ACTIVE</m:PairingStatus>
          </m:CheckInNotification>
          <m:ShippingMethodId>post-a</m:ShippingMethodId>
          <m:CustomerData>
            <m:Field><m:Name>shipping_address</m:Name><m:Value>John|Doe|Rue de l'Écuyer 23A|8004|Zürich|CH</m:Value></m:Field>
            <m:Field><m:Name>date_of_birth</m:Name><m:Value>13/12/1998</m:Value></m:Field>
            <m:Field><m:Name>phone_number</m:Name><m:Value>+41791234567</m:Value></m:Field>
            <m:Field><m:Name>email</m:Name><m:Value>max.mustermann@twint.swiss</m:Value></m:Field>
          </m:CustomerData>
        </m:MonitorFastCheckoutCheckInResponseElement>"""),
    )

    check_in = client.monitor_fast_checkout_check_in(str(PAIRING_UUID))

    assert check_in.is_completed
    assert check_in.shipping_method_id == "post-a"
    data = check_in.customer_data
    assert data.shipping_address == Address("John", "Doe", "Rue de l'Écuyer 23A", "8004", "Zürich", "CH")
    assert data.date_of_birth == date(1998, 12, 13)
    assert data.phone_number == "+41791234567"
    assert data.email == "max.mustermann@twint.swiss"
    # Monitoring doesn't need an enrolled cash register.
    assert transport.actions() == ["MonitorFastCheckoutCheckIn"]


def test_cancel_fast_checkout_check_in(client, transport):
    transport.add("CancelCheckIn", envelope("<m:CancelCheckInResponseElement><m:Status>OK</m:Status></m:CancelCheckInResponseElement>"))

    client.cancel_fast_checkout_check_in(PAIRING_UUID)

    request = transport.last("CancelCheckIn")
    assert text(request, ".//m:Reason") == "PAYMENT_ABORT"
    assert text(request, ".//m:PairingUuid") == str(PAIRING_UUID)


def test_start_fast_checkout_order(client, transport):
    transport.add(
        "StartOrder",
        envelope(f"""<m:StartOrderResponseElement>
          <m:OrderUuid>{ORDER_UUID}</m:OrderUuid>
          <m:OrderStatus>{order_status("IN_PROGRESS", "ORDER_RECEIVED")}</m:OrderStatus>
          <m:CustomerInformation/>
          <m:PairingStatus>PAIRING_ACTIVE</m:PairingStatus>
        </m:StartOrderResponseElement>"""),
    )

    order = client.start_fast_checkout_order(PAIRING_UUID, "order-42", Money.chf("53.5"))

    assert order.id == ORDER_UUID
    assert order.status is OrderStatus.IN_PROGRESS
    assert order.transaction_status is TransactionStatus.ORDER_RECEIVED
    assert order.amount == Money.chf("53.50")

    request = transport.last("StartOrder")
    order_el = request.find(".//m:Order", NS)
    assert order_el.get("type") == "PAYMENT_IMMEDIATE"
    assert order_el.get("confirmationNeeded") == "true"
    assert text(order_el, "m:RequestedAmount/b:Amount") == "53.50"
    assert text(order_el, "m:MerchantTransactionReference") == "order-42"
    assert text(request, ".//m:PairingUuid") == str(PAIRING_UUID)
    assert request.find(".//m:UnidentifiedCustomer", NS) is None


def test_monitor_and_confirm_order(client, transport):
    transport.add(
        "MonitorOrder",
        envelope(f"""<m:MonitorOrderResponseElement>{MERCHANT_INFO}
          {order_type("IN_PROGRESS", "ORDER_CONFIRMATION_PENDING")}
          <m:PairingStatus>PAIRING_ACTIVE</m:PairingStatus>
        </m:MonitorOrderResponseElement>"""),
    )
    transport.add(
        "ConfirmOrder",
        envelope(f"""<m:ConfirmOrderResponseElement>{MERCHANT_INFO}{order_type("SUCCESS", "ORDER_OK")}</m:ConfirmOrderResponseElement>"""),
    )

    order = client.monitor_order(ORDER_UUID)
    assert order.needs_confirmation
    assert order.amount.amount == Decimal("53.50")

    confirmed = client.confirm_order(order, order.amount)
    assert confirmed.is_successful
    assert confirmed.merchant_transaction_reference == "order-42"
    assert text(transport.last("ConfirmOrder"), ".//m:OrderUuid") == str(ORDER_UUID)


def test_monitor_order_by_merchant_reference(client, transport):
    transport.add(
        "MonitorOrder",
        envelope(f"""<m:MonitorOrderResponseElement>{MERCHANT_INFO}
          {order_type("SUCCESS", "ORDER_OK")}
          <m:PairingStatus>NO_PAIRING</m:PairingStatus>
        </m:MonitorOrderResponseElement>"""),
    )

    client.monitor_order("order-42")

    request = transport.last("MonitorOrder")
    assert text(request, ".//m:MerchantTransactionReference") == "order-42"
    assert request.find(".//m:OrderUuid", NS) is None


def test_cancel_order_status_transition_error(client, transport):
    transport.add(
        "CancelOrder",
        """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
          <soapenv:Body><soapenv:Fault>
            <faultcode>soapenv:Server</faultcode><faultstring>Status transition not allowed</faultstring>
            <detail><f:STATUS_TRANSITION_ERROR xmlns:f="http://service.twint.ch/fault/types/v10">
              <f:ErrorCode><f:Status>STATUS_TRANSITION_ERROR</f:Status><f:Code>1200</f:Code></f:ErrorCode>
            </f:STATUS_TRANSITION_ERROR></detail>
          </soapenv:Fault></soapenv:Body>
        </soapenv:Envelope>""",
        status=500,
    )

    with pytest.raises(CancellationFailed):
        client.cancel_order(ORDER_UUID)


def test_soap_fault_is_wrapped(client, transport):
    transport.add(
        "CheckSystemStatus",
        """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
          <soapenv:Body><soapenv:Fault>
            <faultcode>soapenv:Server</faultcode><faultstring>Invalid merchant</faultstring>
          </soapenv:Fault></soapenv:Body>
        </soapenv:Envelope>""",
        status=500,
    )

    with pytest.raises(ApiFailure, match="Invalid merchant"):
        client.check_system_status()


def test_reverse_order(client, transport):
    reversal_uuid = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    transport.add(
        "StartOrder",
        envelope(f"""<m:StartOrderResponseElement>
          <m:OrderUuid>{reversal_uuid}</m:OrderUuid>
          <m:OrderStatus>{order_status("SUCCESS", "ORDER_OK")}</m:OrderStatus>
          <m:CustomerInformation/>
          <m:PairingStatus>NO_PAIRING</m:PairingStatus>
        </m:StartOrderResponseElement>"""),
    )

    reversal = client.reverse_order("order-42-refund-1", ORDER_UUID, Money.chf(10))

    assert reversal.id == reversal_uuid
    assert reversal.is_successful
    order_el = transport.last("StartOrder").find(".//m:Order", NS)
    assert order_el.get("type") == "REVERSAL"
    assert order_el.get("confirmationNeeded") == "false"
    assert text(order_el, "m:Link/m:OrderUuid") == str(ORDER_UUID)


def test_cash_register_is_enrolled_once(client, transport):
    transport.add(
        "CheckSystemStatus", envelope("<m:CheckSystemStatusResponseElement><m:Status>OK</m:Status></m:CheckSystemStatusResponseElement>")
    )
    transport.add(
        "MonitorOrder",
        envelope(f"""<m:MonitorOrderResponseElement>{MERCHANT_INFO}
          {order_type("SUCCESS", "ORDER_OK")}
          <m:PairingStatus>NO_PAIRING</m:PairingStatus>
        </m:MonitorOrderResponseElement>"""),
    )

    client.monitor_order(ORDER_UUID)
    client.monitor_order(ORDER_UUID)

    assert transport.actions() == ["EnrollCashRegister", "MonitorOrder", "MonitorOrder"]
    enroll = transport.last("EnrollCashRegister")
    assert text(enroll, ".//m:CashRegisterType") == "EPOS"
