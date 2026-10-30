import time
import random
import re
import requests
import uuid
import base64
import json
import os
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urljoin, urlsplit

API_URL = "https://apptoogoodtogo.com/api/"
DATADOME_SDK_URL = "https://api-sdk.datadome.co/sdk/"
DEFAULT_APK_VERSION = "26.7.2"
USER_AGENT = f"TGTG/{DEFAULT_APK_VERSION} Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Pro Build/UP1A.231005.007)"

AUTH_BY_EMAIL_ENDPOINT = "auth/v5/authByEmail"
AUTH_BY_REQUEST_PIN_ENDPOINT = "auth/v5/authByRequestPin"
API_ITEM_ENDPOINT = "item/v8/"

API_BUCKET_ENDPOINT = "discover/v1/bucket"
CREATE_ORDER_ENDPOINT = "order/v8/create/{}"
ORDER_STATUS_ENDPOINT = "order/v8/{}/status"
ABORT_ORDER_ENDPOINT = "order/v8/{}/abort"
REFRESH_ENDPOINT = "token/v1/refresh"
TOKEN_REFRESH_MARGIN_SECONDS = 60


class TgtgBlockedError(Exception):
    """The upstream service still refuses the request after one cookie retry."""

    def __init__(self):
        super().__init__(
            "API Error 403: Too Good To Go blocked this request after one cookie refresh. "
            "Stop automated retries and check your account in the official app. "
            "If the app works, retry the dashboard later; repeated login attempts may not help."
        )


class TgtgAuthError(Exception):
    """The saved login cannot be renewed; a new email/PIN login is needed."""


def token_expiry(token):
    """Read the JWT expiry for diagnostics only; this does not verify its signature."""
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))["exp"])
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None

def _generate_datadome_cid():
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789~_"
    return "".join(random.choice(chars) for _ in range(120))


def _get_user_id(token):
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data["sub"])
    except Exception:
        return None


class TgtgApiClient:
    def __init__(self, access_token=None, refresh_token=None, cookie=None):
        self.session = requests.Session()
        self.device_type = "ANDROID"
        self.apk_version = os.environ.get("TGTG_APK_VERSION") or DEFAULT_APK_VERSION
        self.user_agent = os.environ.get("TGTG_USER_AGENT") or USER_AGENT.replace(
            f"TGTG/{DEFAULT_APK_VERSION}", f"TGTG/{self.apk_version}", 1
        )
        version = re.match(r"TGTG/([\d.]+)\s", self.user_agent)
        if version:
            self.apk_version = version.group(1)
        self.correlation_id = uuid.uuid4().hex
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user_id = _get_user_id(self.access_token)
        if cookie:
            # Accept existing temp.json values, including raw Set-Cookie strings.
            # A manual Cookie header would override the jar even after a 403 refresh.
            self._store_datadome_cookie(cookie)

    def _store_datadome_cookie(self, cookie_header):
        parsed = SimpleCookie()
        try:
            parsed.load(cookie_header)
        except CookieError:
            return
        if "datadome" not in parsed:
            return
        for cookie in list(self.session.cookies):
            if cookie.name == "datadome":
                self.session.cookies.clear(cookie.domain, cookie.path, cookie.name)
        self.session.cookies.set(
            "datadome", parsed["datadome"].value,
            domain=f".{urlsplit(API_URL).hostname}", path="/", secure=True,
        )

    def _fetch_datadome_cookie(self, request_url):
        cid = _generate_datadome_cid()
        params = {
            "camera": '{"auth":"true", "info":"{\\"front\\":\\"2000x1500\\",\\"back\\":\\"5472x3648\\"}"}',
            "cid": cid,
            "ddk": "1D42C2CA6131C526E09F294FE96F94",
            "ddv": "3.0.4",
            "ddvc": self.apk_version,
            "events": '[{"id":1,"message":"response validation","source":"sdk","date":'
            + str(int(time.time() * 1000))
            + "}]",
            "inte": "android-java-okhttp",
            "mdl": "Pixel 7 Pro",
            "os": "Android",
            "osn": "UPSIDE_DOWN_CAKE",
            "osr": "14",
            "osv": "34",
            "request": request_url,
            "screen_d": "3.5",
            "screen_x": "1440",
            "screen_y": "3120",
            "ua": self.user_agent,
        }
        try:
            r = requests.post(
                DATADOME_SDK_URL,
                data=params,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "gzip, deflate, br",
                },
                timeout=10,
            )
            data = r.json()
            if data.get("status") == 200 and data.get("cookie"):
                self._store_datadome_cookie(data["cookie"])
        except Exception as e:
            print("Failed to fetch DataDome cookie:", e)

    def _ensure_datadome_cookie(self, request_url):
        if "datadome" not in self.session.cookies:
            self._fetch_datadome_cookie(request_url)

    def _get_headers(self):
        headers = {
            "accept": "application/json",
            "accept-language": "en-US",
            "content-type": "application/json; charset=utf-8",
            "user-agent": self.user_agent,
            "x-correlation-id": self.correlation_id,
        }
        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"
        return headers

    def get_credentials(self):
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "cookie": "; ".join(f"{c.name}={c.value}" for c in self.session.cookies if c.name == "datadome"),
            "user_id": self.user_id,
        }

    def _post(self, url, _allow_refresh=True, **kwargs):
        kwargs.setdefault("timeout", 20)
        refreshed = self.refresh_if_needed() if _allow_refresh else False
        self._ensure_datadome_cookie(url)
        cookie_retried = False
        # Handle both 401 -> 403 and 403 -> 401, without unbounded retries.
        while True:
            response = self.session.post(url, headers=self._get_headers(), **kwargs)
            if response.status_code == 401 and _allow_refresh and not refreshed:
                self._refresh()
                refreshed = True
                continue
            if response.status_code == 403 and not cookie_retried:
                self.session.cookies.clear()
                self._fetch_datadome_cookie(url)
                cookie_retried = True
                continue
            break
        if response.status_code == 403:
            raise TgtgBlockedError()
        if response.status_code == 401:
            raise TgtgAuthError("Login expired or invalid. Reconnect with email and PIN in Authentication or the notebook.")
        if response.status_code >= 400:
            raise Exception(f"API Error {response.status_code}: {response.text}")
        return response

    def _refresh(self):
        if not self.refresh_token:
            raise TgtgAuthError("No refresh token saved. Please log in with email and PIN.")
        url = urljoin(API_URL, REFRESH_ENDPOINT)
        resp = self._post(url, _allow_refresh=False, json={"refresh_token": self.refresh_token})
        if resp.status_code == 200:
            data = resp.json()
            if not data.get("access_token"):
                raise TgtgAuthError("Token refresh returned no access token. Please log in again.")
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token") or self.refresh_token
            self.user_id = _get_user_id(self.access_token)
        else:
            raise TgtgAuthError("Token refresh failed. Please log in again.")

    def refresh_credentials(self):
        self._refresh()
        return self.get_credentials()

    def refresh_if_needed(self):
        """Renew before a request when JWT expiry is within one minute.

        Unknown expiry falls back to the bounded 401 recovery in _post.
        No background requests are needed while the account is idle.
        """
        expires = token_expiry(self.access_token)
        if expires is None or expires > time.time() + TOKEN_REFRESH_MARGIN_SECONDS:
            return False
        self._refresh()
        renewed_expiry = token_expiry(self.access_token)
        if renewed_expiry is not None and renewed_expiry <= time.time():
            raise TgtgAuthError("Refreshed access token is already expired. Please reconnect your account.")
        return True

    def request_auth_code(self, email):
        url = urljoin(API_URL, AUTH_BY_EMAIL_ENDPOINT)
        response = self._post(url, _allow_refresh=False, json={"device_type": self.device_type, "email": email})
        return response.json()

    def submit_pin(self, email, pin, polling_id):
        url = urljoin(API_URL, AUTH_BY_REQUEST_PIN_ENDPOINT)
        response = self._post(url, _allow_refresh=False, json={
            "device_type": self.device_type,
            "email": email,
            "request_pin": pin,
            "request_polling_id": polling_id,
        })
        data = response.json()
        if not data.get("access_token") or not data.get("refresh_token"):
            raise TgtgAuthError("Login did not return both tokens. Request a new PIN and try again.")
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        self.user_id = _get_user_id(self.access_token)
        
        self._store_datadome_cookie(response.headers.get("Set-Cookie", ""))
        # The cookie may come from the SDK rather than the PIN response.
        cookie_string = "; ".join(
            f"{cookie.name}={cookie.value}" for cookie in self.session.cookies
            if cookie.name == "datadome"
        )
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "cookie": cookie_string,
            "user_id": self.user_id
        }

    def get_items(self, latitude=48.8566, longitude=2.3522):
        url = urljoin(API_URL, API_ITEM_ENDPOINT)
        data = {
            "favorites_only": False,
            "origin": {"latitude": latitude, "longitude": longitude},
            "radius": 5,
            "discover": False,
            "hidden_only": False,
            "we_care_only": False,
        }
        response = self._post(url, json=data)
        return response.json().get("items", [])

    def get_item(self, item_id):
        url = urljoin(API_URL, f"item/v8/{item_id}")
        response = self._post(url, json={"item_id": item_id})
        return response.json()

    def get_favorites(self, latitude=48.8566, longitude=2.3522):
        url = urljoin(API_URL, API_BUCKET_ENDPOINT)
        data = {
            "origin": {"latitude": latitude, "longitude": longitude},
            "radius": 5,
            "user_id": self.user_id,
            "paging": {"page": 0, "size": 50},
            "bucket": {"filler_type": "Favorites"},
        }
        response = self._post(url, json=data)
        return response.json().get("mobile_bucket", {}).get("items", [])

    def create_order(self, item_id, item_count=1):
        url = urljoin(API_URL, CREATE_ORDER_ENDPOINT.format(item_id))
        response = self._post(url, json={"item_count": item_count})
        return response.json()

    def get_order_status(self, order_id):
        url = urljoin(API_URL, ORDER_STATUS_ENDPOINT.format(order_id))
        response = self._post(url)
        return response.json()

    def get_active_orders(self):
        url = urljoin(API_URL, "order/v8/active")
        response = self._post(url, json={"page": 0, "size": 20})
        return response.json().get("orders", [])

    def abort_order(self, order_id):
        url = urljoin(API_URL, ABORT_ORDER_ENDPOINT.format(order_id))
        response = self._post(url, json={"cancel_reason_id": 1})
        return response.json()
