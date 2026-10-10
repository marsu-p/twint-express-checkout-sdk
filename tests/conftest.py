from __future__ import annotations

from uuid import UUID

import pytest
import requests
import zeep
from lxml import etree

from twint_sdk import Certificate, Environment, MerchantInformation, TwintClient

STORE_UUID = UUID("2a6cd5a6-6c5e-4f6b-9d3c-0f1c3c4d5e6f")
MESSAGE_ID = UUID("00000000-0000-4000-8000-000000000001")

NS = {
    "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
    "h": "http://service.twint.ch/header/types/v10",
    "m": "http://service.twint.ch/merchant/types/v10",
    "b": "http://service.twint.ch/base/types/v10",
}


def envelope(body: str) -> str:
    return f"""<soapenv:Envelope xmlns:soapenv="{NS['soapenv']}" xmlns:h="{NS['h']}"
        xmlns:m="{NS['m']}" xmlns:b="{NS['b']}">
      <soapenv:Header>
        <h:ResponseHeaderElement><h:MessageId>{MESSAGE_ID}</h:MessageId></h:ResponseHeaderElement>
      </soapenv:Header>
      <soapenv:Body>{body}</soapenv:Body>
    </soapenv:Envelope>"""


ENROLL_OK = envelope("<m:EnrollCashRegisterResponseElement/>")


class FakeTransport(zeep.Transport):
    """Records requests and answers each SOAPAction with a queued canned response."""

    def __init__(self) -> None:
        super().__init__()
        self.responses: dict[str, list[tuple[int, str]]] = {}
        self.requests: list[tuple[str, etree._Element]] = []

    def add(self, action: str, body: str, status: int = 200) -> None:
        self.responses.setdefault(action, []).append((status, body))

    def post_xml(self, address, envelope, headers):
        action = headers["SOAPAction"].strip('"')
        self.requests.append((action, envelope))
        queue = self.responses.get(action)
        assert queue, f"Unexpected SOAP call {action}"
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        response = requests.Response()
        response.status_code = status
        response._content = body.encode()
        response.headers["Content-Type"] = "text/xml; charset=utf-8"
        response.url = address
        return response

    def last(self, action: str) -> etree._Element:
        return [e for a, e in self.requests if a == action][-1]

    def actions(self) -> list[str]:
        return [a for a, _ in self.requests]


@pytest.fixture(autouse=True)
def reset_enrollment():
    from twint_sdk import client

    client._enrolled_cash_registers.clear()


@pytest.fixture
def transport() -> FakeTransport:
    t = FakeTransport()
    t.add("EnrollCashRegister", ENROLL_OK)
    return t


@pytest.fixture
def client(transport: FakeTransport) -> TwintClient:
    return TwintClient(
        Certificate(b"", b""),
        MerchantInformation(STORE_UUID, "webshop"),
        Environment.TESTING,
        transport=transport,
        message_id_factory=lambda: MESSAGE_ID,
    )


def text(element: etree._Element, path: str) -> str | None:
    found = element.find(path, NS)
    return None if found is None else found.text
