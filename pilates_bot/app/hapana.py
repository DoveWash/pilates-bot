"""Client for the Hapana widget API (the booking backend the studio uses).

Same calls as the original bot: security token -> login -> list sessions -> checkout,
but with timeouts, retries on transient errors and clear error types.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://widgetapi.hapana.com/v2/wAPI"
PAGE_SIZE = 200


class HapanaError(Exception):
    """retryable=True means trying again later might work (network, 5xx, rate limit)."""

    def __init__(self, message: str, retryable: bool = True, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)


@dataclass
class Auth:
    security_token: str
    access_token: str
    client_id: str
    cookie: str | None


@dataclass
class BookingResult:
    success: bool
    message: str


def _message(data) -> str:
    if not isinstance(data, dict):
        return ""
    inner = data.get("data")
    msg = data.get("message") or (inner.get("message") if isinstance(inner, dict) else None)
    return str(msg) if msg else ""


class HapanaClient:
    """One client per member: requests.Session keeps cookies, so never share it between people."""

    def __init__(self, widget_id: str, timeout: float = 20, attempts: int = 3, backoff: float = 1.0):
        self.widget_id = widget_id
        self.timeout = timeout
        self.attempts = attempts
        self.backoff = backoff
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "pilates-bot/2"

    # -- plumbing ---------------------------------------------------------
    def _call(self, method: str, path: str, *, headers=None, params=None, json_body=None, attempts=None):
        tries = attempts or self.attempts
        last: HapanaError | None = None
        for i in range(tries):
            try:
                resp = self.http.request(
                    method, BASE_URL + path, headers=headers, params=params, json=json_body, timeout=self.timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = HapanaError(f"Network error: {exc.__class__.__name__}", retryable=True)
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = HapanaError(f"Booking site returned HTTP {resp.status_code}", True, resp.status_code)
                else:
                    try:
                        data = resp.json()
                    except ValueError:
                        raise HapanaError(
                            f"Unexpected non-JSON reply (HTTP {resp.status_code})", False, resp.status_code
                        ) from None
                    if resp.status_code in (401, 403):
                        raise HapanaError(_message(data) or "Not authorised", False, resp.status_code)
                    return resp, data
            if i < tries - 1:
                time.sleep(self.backoff * (2**i))
        assert last is not None
        raise last

    @staticmethod
    def _auth_headers(auth: Auth) -> dict:
        h = {
            "content-type": "application/json",
            "authorization": "Bearer " + auth.access_token,
            "bypasstoken": "false",
            "securitytoken": auth.security_token,
        }
        if auth.cookie:
            h["Cookie"] = "PHPSESSID=" + auth.cookie
        return h

    # -- API --------------------------------------------------------------
    def security_token(self) -> str:
        _, data = self._call("GET", "/site/settings", headers={"wid": self.widget_id})
        token = data.get("securityToken") if isinstance(data, dict) else None
        if not token:
            raise HapanaError("No securityToken in site settings reply", retryable=True)
        return token

    def login(self, email: str, password: str) -> Auth:
        token = self.security_token()
        resp, data = self._call(
            "POST",
            "/auth/login/",
            headers={"securityToken": token, "Content-Type": "application/json"},
            json_body={"email": email, "password": password},
        )
        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict) or not inner.get("accessToken"):
            raise HapanaError("Login failed: " + (_message(data) or "check email/password"), retryable=False)
        return Auth(
            security_token=token,
            access_token=inner["accessToken"],
            client_id=str(inner.get("clientID", "")),
            cookie=resp.cookies.get("PHPSESSID"),
        )

    def list_sessions(self, auth: Auth, site_id: str, start: date, end: date) -> list[dict]:
        out: list[dict] = []
        for page in range(1, 11):
            _, data = self._call(
                "GET",
                "/site/sessions",
                headers=self._auth_headers(auth),
                params={
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "sessionCategory": "classes",
                    "siteID": site_id,
                    "sessionType": "Classes",
                    "pageIndex": page,
                    "pageSize": PAGE_SIZE,
                    "clientID": auth.client_id,  # the old Yarralumla call forgot this
                },
            )
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise HapanaError("Unexpected class list reply: " + (_message(data) or "no data"), retryable=True)
            out.extend(items)
            if len(items) < PAGE_SIZE:
                break
        return out

    def checkout(self, auth: Auth, session: dict, site_id: str, package_subscription_id: str) -> BookingResult:
        payload = {
            "paymentType": "sessions",
            "applyAccountCredits": False,
            "sessionDate": session.get("sessionDate"),
            "paymentMethod": "validcredits",
            "checkoutID": session.get("sessionID"),
            "packageSubscriptionID": package_subscription_id,
            "siteID": site_id,
            "clientID": auth.client_id,
        }
        headers = {
            "authorization": "Bearer " + auth.access_token,
            "content-type": "application/json",
            "securitytoken": auth.security_token,
        }
        _, data = self._call(
            "POST", "/payments/checkout", headers=headers, params={"clientID": auth.client_id},
            json_body=payload, attempts=2,
        )
        ok = bool(data.get("success")) if isinstance(data, dict) else False
        return BookingResult(ok, _message(data) or ("Booked" if ok else "No reason given"))
