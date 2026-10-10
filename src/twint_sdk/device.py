"""Helpers to open the TWINT app directly on mobile instead of showing a QR code."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode

import requests

from .environment import Environment
from .exceptions import ApiFailure


class Device(str, Enum):
    IOS = "IOS"
    ANDROID = "ANDROID"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class IosAppScheme:
    scheme: str
    display_name: str


def detect_device(user_agent: str) -> Device:
    if "iPhone" in user_agent or "iPad" in user_agent:
        return Device.IOS
    if "Android" in user_agent:
        return Device.ANDROID
    return Device.UNKNOWN


def get_ios_app_schemes(environment: Environment, timeout: float = 10) -> list[IosAppScheme]:
    """iOS has one TWINT app per bank, so the customer must pick theirs from this list."""
    try:
        response = requests.get(environment.app_scheme_url, timeout=timeout)
        response.raise_for_status()
        configs = response.json()["appSwitchConfigList"]
        return [IosAppScheme(c["issuerUrlScheme"], c["displayName"]) for c in configs]
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise ApiFailure(f"Failed to fetch iOS app schemes: {e}") from e


def ios_app_url(scheme: IosAppScheme | str, pairing_token: str) -> str:
    scheme = scheme.scheme if isinstance(scheme, IosAppScheme) else scheme
    payload = {
        "app_action_type": "TWINT_PAYMENT",
        "extras": {"code": pairing_token},
        "referer_app_link": {"target_url": "", "url": "", "app_name": "EXTERNAL_WEB_BROWSER"},
        "version": "6.0",
    }
    return f"{scheme}applinks/?al_applink_data={json.dumps(payload, separators=(',', ':'))}"


def android_app_url(pairing_token: str) -> str:
    params = urlencode(
        {
            "action": "ch.twint.action.TWINT_PAYMENT",
            "scheme": "twint",
            "S.code": pairing_token,
            "S.startingOrigin": "EXTERNAL_WEB_BROWSER",
            "S.browser_fallback_url": "",
        }
    ).replace("&", ";")
    return f"intent://payment#Intent;{params};end"
