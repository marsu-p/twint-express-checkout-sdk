from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Union
from uuid import UUID

from .exceptions import InvalidValue


class Currency(str, Enum):
    CHF = "CHF"


@dataclass(frozen=True)
class Money:
    currency: Currency
    amount: Decimal

    def __post_init__(self) -> None:
        # Accept ints, floats and strings for convenience, but always store a Decimal.
        object.__setattr__(self, "amount", Decimal(str(self.amount)))

    @classmethod
    def chf(cls, amount: Decimal | int | float | str) -> Money:
        return cls(Currency.CHF, Decimal(str(amount)))

    def __add__(self, other: Money) -> Money:
        if self.currency is not other.currency:
            raise InvalidValue(f"Currencies must match, got {self.currency} and {other.currency}")
        return Money(self.currency, self.amount + other.amount)

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency.value}"

    def to_soap(self) -> dict:
        return {"Amount": self.amount.quantize(Decimal("0.01")), "Currency": self.currency.value}

    @classmethod
    def from_soap(cls, value) -> Money:
        return cls(Currency(value.Currency), Decimal(str(value.Amount)))


class SystemStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"


class PairingStatus(str, Enum):
    NO_PAIRING = "NO_PAIRING"
    PAIRING_IN_PROGRESS = "PAIRING_IN_PROGRESS"
    PAIRING_ACTIVE = "PAIRING_ACTIVE"


class OrderStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    FAILURE = "FAILURE"
    SUCCESS = "SUCCESS"


class TransactionStatus(str, Enum):
    ORDER_OK = "ORDER_OK"
    ORDER_PARTIAL_OK = "ORDER_PARTIAL_OK"
    ORDER_RECEIVED = "ORDER_RECEIVED"
    ORDER_PENDING = "ORDER_PENDING"
    ORDER_CONFIRMATION_PENDING = "ORDER_CONFIRMATION_PENDING"
    GENERAL_ERROR = "GENERAL_ERROR"
    CLIENT_TIMEOUT = "CLIENT_TIMEOUT"
    MERCHANT_ABORT = "MERCHANT_ABORT"
    CUSTOMER_ABORT = "CUSTOMER_ABORT"
    CLIENT_ABORT = "CLIENT_ABORT"


class CustomerDataScope(str, Enum):
    SHIPPING_ADDRESS = "shipping_address"
    DATE_OF_BIRTH = "date_of_birth"
    PHONE_NUMBER = "phone_number"
    EMAIL = "email"


@dataclass(frozen=True)
class ShippingMethod:
    id: str
    label: str
    price: Money

    def to_soap(self) -> dict:
        return {
            "ShippingMethodId": self.id,
            "ShippingMethodLabel": self.label,
            "ShippingMethodAmount": self.price.to_soap(),
        }


_ADDRESS_RE = re.compile(
    r"^(?P<first_name>.+?)\|(?P<last_name>.+?)\|(?P<street>.+?)\|(?P<zip>.+?)\|(?P<city>.+?)\|(?P<country>[A-Z]{2})$"
)


@dataclass(frozen=True)
class Address:
    first_name: str
    last_name: str
    street: str
    zip: str
    city: str
    country: str  # ISO 3166-1 alpha-2

    @classmethod
    def parse(cls, value: str) -> Address:
        """Parse TWINT's pipe-separated address, e.g. ``Max|Muster|Bahnhofstrasse 1|8001|Zürich|CH``."""
        match = _ADDRESS_RE.match(value)
        if match is None:
            raise InvalidValue(f"Cannot parse address {value!r}")
        return cls(**match.groupdict())


@dataclass(frozen=True)
class CustomerData:
    shipping_address: Address | None = None
    date_of_birth: date | None = None
    phone_number: str | None = None
    email: str | None = None

    @classmethod
    def from_fields(cls, fields: dict[str, str]) -> CustomerData:
        kwargs: dict = {}
        for name, value in fields.items():
            if name == CustomerDataScope.SHIPPING_ADDRESS:
                kwargs["shipping_address"] = Address.parse(value)
            elif name == CustomerDataScope.DATE_OF_BIRTH:
                kwargs["date_of_birth"] = _parse_date(value)
            elif name == CustomerDataScope.PHONE_NUMBER:
                kwargs["phone_number"] = value
            elif name == CustomerDataScope.EMAIL:
                kwargs["email"] = value
            # Unknown fields are ignored so new scopes don't break existing integrations.
        return cls(**kwargs)


def _parse_date(value: str) -> date:
    """TWINT sends dates of birth as ``DD/MM/YYYY``."""
    try:
        day, month, year = value.split("/")
        return date(int(year), int(month), int(day))
    except ValueError as e:
        raise InvalidValue(f"Cannot parse date {value!r}") from e


@dataclass(frozen=True)
class InteractiveFastCheckoutCheckIn:
    """Result of starting an express checkout: show ``qr_code`` or redirect to ``payment_url``."""

    pairing_uuid: UUID
    pairing_status: PairingStatus
    pairing_token: str
    qr_code: str | None = None
    payment_url: str | None = None


@dataclass(frozen=True)
class FastCheckoutCheckIn:
    """State of an express checkout while polling it with ``monitor_fast_checkout_check_in``."""

    pairing_uuid: UUID
    pairing_status: PairingStatus
    shipping_method_id: str | None = None
    customer_data: CustomerData | None = None

    @property
    def is_paired(self) -> bool:
        return self.pairing_status is PairingStatus.PAIRING_ACTIVE

    @property
    def is_completed(self) -> bool:
        """The customer has confirmed the check-in in the app and shared their data."""
        return self.is_paired and self.customer_data is not None


@dataclass(frozen=True)
class Order:
    id: UUID
    merchant_transaction_reference: str
    status: OrderStatus
    transaction_status: TransactionStatus
    amount: Money
    pairing_status: PairingStatus | None = None
    pairing_token: str | None = None
    qr_code: str | None = None
    payment_url: str | None = None

    @property
    def is_successful(self) -> bool:
        return self.status is OrderStatus.SUCCESS

    @property
    def is_failed(self) -> bool:
        return self.status is OrderStatus.FAILURE

    @property
    def is_pending(self) -> bool:
        return self.status is OrderStatus.IN_PROGRESS

    @property
    def needs_confirmation(self) -> bool:
        return self.transaction_status is TransactionStatus.ORDER_CONFIRMATION_PENDING


@dataclass(frozen=True)
class OrderReference:
    """Identifies an existing order either by TWINT's order UUID or by your own reference."""

    order_id: UUID | None = None
    merchant_transaction_reference: str | None = None

    def __post_init__(self) -> None:
        if (self.order_id is None) == (self.merchant_transaction_reference is None):
            raise InvalidValue("Exactly one of order_id or merchant_transaction_reference must be set")

    def to_soap(self) -> dict:
        if self.order_id is not None:
            return {"OrderUuid": str(self.order_id)}
        return {"MerchantTransactionReference": self.merchant_transaction_reference}


OrderRef = Union[Order, OrderReference, UUID, str]
"""Anything that identifies an order: an ``Order``, an ``OrderReference``, a TWINT order UUID,
or your own merchant transaction reference as ``str``."""


def to_order_reference(ref: OrderRef) -> OrderReference:
    if isinstance(ref, OrderReference):
        return ref
    if isinstance(ref, Order):
        return OrderReference(order_id=ref.id)
    if isinstance(ref, UUID):
        return OrderReference(order_id=ref)
    return OrderReference(merchant_transaction_reference=ref)


@dataclass(frozen=True)
class MerchantInformation:
    store_uuid: UUID
    cash_register_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.cash_register_id:
            # Same default as the official PHP SDK.
            object.__setattr__(self, "cash_register_id", f"Unknown-{self.store_uuid}")
        if len(self.cash_register_id) > 50:
            raise InvalidValue("cash_register_id must be at most 50 characters long")

    def to_soap(self) -> dict:
        return {"MerchantUuid": str(self.store_uuid), "CashRegisterId": self.cash_register_id}
