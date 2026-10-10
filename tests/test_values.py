from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from twint_sdk import Address, InvalidValue, MerchantInformation, Money, OrderReference
from twint_sdk.device import Device, android_app_url, detect_device, ios_app_url


def test_money_normalises_amounts():
    assert Money.chf(10.1).amount == Decimal("10.1")
    assert Money.chf("10.10").to_soap() == {"Amount": Decimal("10.10"), "Currency": "CHF"}
    assert str(Money.chf(3) + Money.chf("0.5")) == "3.50 CHF"


def test_address_parse_rejects_garbage():
    with pytest.raises(InvalidValue):
        Address.parse("not an address")


def test_order_reference_requires_exactly_one_identifier():
    with pytest.raises(InvalidValue):
        OrderReference()
    with pytest.raises(InvalidValue):
        OrderReference(order_id=UUID(int=1), merchant_transaction_reference="x")


def test_merchant_information_default_cash_register_id():
    store = UUID("2a6cd5a6-6c5e-4f6b-9d3c-0f1c3c4d5e6f")
    assert MerchantInformation(store).cash_register_id == f"Unknown-{store}"


@pytest.mark.parametrize(
    ("user_agent", "device"),
    [
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", Device.IOS),
        ("Mozilla/5.0 (Linux; Android 14; Pixel 8)", Device.ANDROID),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)", Device.UNKNOWN),
    ],
)
def test_detect_device(user_agent, device):
    assert detect_device(user_agent) is device


def test_app_urls():
    assert android_app_url("12345") == (
        "intent://payment#Intent;action=ch.twint.action.TWINT_PAYMENT;scheme=twint;S.code=12345;"
        "S.startingOrigin=EXTERNAL_WEB_BROWSER;S.browser_fallback_url=;end"
    )
    assert ios_app_url("twint-issuer1://", "12345") == (
        'twint-issuer1://applinks/?al_applink_data={"app_action_type":"TWINT_PAYMENT","extras":{"code":"12345"},'
        '"referer_app_link":{"target_url":"","url":"","app_name":"EXTERNAL_WEB_BROWSER"},"version":"6.0"}'
    )
