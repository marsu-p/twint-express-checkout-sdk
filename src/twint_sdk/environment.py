from __future__ import annotations

from enum import Enum
from pathlib import Path

_WSDL_DIR = Path(__file__).parent / "resources" / "wsdl"


class Version(Enum):
    V10 = "10"

    @property
    def wsdl_path(self) -> Path:
        return _WSDL_DIR / f"v{self.value}" / f"TWINTMerchantService_v{self.value}.wsdl"

    @property
    def merchant_namespace(self) -> str:
        return f"http://service.twint.ch/merchant/v{self.value}"


class Environment(Enum):
    TESTING = "TESTING"
    PRODUCTION = "PRODUCTION"

    @property
    def service_host(self) -> str:
        return "service-pat.twint.ch" if self is Environment.TESTING else "service.twint.ch"

    @property
    def app_scheme_url(self) -> str:
        host = "app.scheme-pat.twint.ch" if self is Environment.TESTING else "app.scheme.twint.ch"
        return f"https://{host}/appSwitch/v1/configs"

    def soap_endpoint(self, version: Version) -> str:
        return f"https://{self.service_host}/merchant/service/TWINTMerchantServiceV{version.value}"
