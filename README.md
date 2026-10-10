# twint-express-checkout-sdk

Python client for the TWINT Merchant API (SOAP, v10), with support for **Express Checkout**
(called *FastCheckout* in the API). It is a port of the official
[TWINT PHP SDK](https://github.com/Twint-AG/sdk), which the WooCommerce, Magento and Shopware
plugins are built on, and it bundles that SDK's WSDL files.

## Installation

```console
uv add git+https://github.com/marsu-p/twint-express-checkout-sdk
```

## Credentials

From the TWINT merchant portal you need:

* your **store UUID**
* a **client certificate** (`.p12` file plus password), which is used for mutual TLS

Use `Environment.TESTING` (`service-pat.twint.ch`) with test credentials, and
`Environment.PRODUCTION` for live payments.

## Express checkout flow

```python
from uuid import UUID

from twint_sdk import (
    Certificate, CustomerDataScope, Environment, MerchantInformation, Money,
    ShippingMethod, TwintClient,
)

client = TwintClient(
    Certificate.from_pkcs12("merchant.p12", "p12-password"),
    MerchantInformation(store_uuid=UUID("..."), cash_register_id="webshop"),
    Environment.TESTING,
)

# 1. Customer clicks "Pay with TWINT" on the cart page.
check_in = client.request_fast_checkout_check_in(
    Money.chf("49.90"),                       # cart total without shipping
    [CustomerDataScope.EMAIL, CustomerDataScope.SHIPPING_ADDRESS, CustomerDataScope.PHONE_NUMBER],
    [ShippingMethod("post-a", "A-Post", Money.chf("3.60")),
     ShippingMethod("post-b", "B-Post", Money.chf("2.20"))],
)
# Show check_in.qr_code (a data: URI) on desktop. On mobile, use twint_sdk.device to open the
# app with check_in.pairing_token. Pass hosted=True to get check_in.payment_url, a TWINT-hosted
# page, instead.

# 2. Poll from the browser every 1-2 s until the customer confirms in the app.
state = client.monitor_fast_checkout_check_in(check_in.pairing_uuid)
if state.is_completed:
    address = state.customer_data.shipping_address
    shipping_id = state.shipping_method_id
    # ... create your order with these details

# 3. Charge the final amount (including shipping).
order = client.start_fast_checkout_order(check_in.pairing_uuid, "order-42", Money.chf("53.50"))

# 4. Poll the order, then confirm it once TWINT has reserved the money.
order = client.monitor_order(order)
if order.needs_confirmation:
    order = client.confirm_order(order, order.amount)
assert order.is_successful
```

If the customer abandons the checkout, call `client.cancel_fast_checkout_check_in(pairing_uuid)`
or `client.cancel_order(order)`.

## Other operations

* `start_order(reference, amount, hosted=False)`: regular (non-express) checkout
* `reverse_order(refund_reference, order, amount)`: full or partial refund
* `check_system_status()`
* `twint_sdk.device`: `detect_device`, `android_app_url`, `ios_app_url`, `get_ios_app_schemes`

All API errors are raised as `twint_sdk.ApiFailure`. A cancellation that is no longer possible
raises `CancellationFailed`.

## Development

```console
uv sync
uv run pytest
```

The tests use canned SOAP responses and never call the TWINT API.
