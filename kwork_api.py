"""Kwork HTTP API client — all interactions with api.kwork.ru."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger("kwork-mcp")

# ── Exceptions ─────────────────────────────────────────────────────────────

class KworkError(Exception):
    pass

class KworkAuthError(KworkError):
    pass

class KworkApiError(KworkError):
    pass

# ── Config ─────────────────────────────────────────────────────────────────

ENV_FILE = Path(__file__).parent / ".env"
TOKEN_FILE = Path(__file__).parent / ".kwork_token.json"

API_BASE = "https://api.kwork.ru"
BASIC_AUTH = ("mobile_api", "qFvfRl7w")  # Public mobile API credentials (same for all users)


def _load_env() -> tuple[str, str]:
    """Load KWORK_LOGIN and KWORK_PASSWORD from .env or environment."""
    login = os.getenv("KWORK_LOGIN", "")
    password = os.getenv("KWORK_PASSWORD", "")
    if not login and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("KWORK_LOGIN="):
                login = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("KWORK_PASSWORD="):
                password = line.split("=", 1)[1].strip().strip('"')
    return login, password


# ── API Client ─────────────────────────────────────────────────────────────

class KworkApi:
    """Async HTTP client for api.kwork.ru with token auto-refresh."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._token: str = ""
        self._token_expires: float = 0
        self._lock = asyncio.Lock()
        self._login, self._password = _load_env()
        self._user_id: int | None = None

    async def connect(self):
        """Create HTTP client and load cached token."""
        self._client = httpx.AsyncClient(timeout=30)
        # Try loading cached token
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text())
                if data.get("expires", 0) > time.time() + 3600:
                    self._token = data["token"]
                    self._token_expires = data["expires"]
                    log.info("Loaded cached API token")
            except (json.JSONDecodeError, KeyError):
                pass

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Auth ───────────────────────────────────────────────────────────────

    async def _sign_in(self) -> str:
        """Authenticate and get API token."""
        if not self._login or not self._password:
            raise KworkAuthError("KWORK_LOGIN/KWORK_PASSWORD not set in .env")
        resp = await self._client.post(
            f"{API_BASE}/signIn",
            auth=BASIC_AUTH,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"login": self._login, "password": self._password},
        )
        body = resp.json()
        if not body.get("success"):
            raise KworkAuthError(f"Sign in failed: {body.get('error', body)}")
        token_data = body["response"]
        self._token = token_data["token"]
        self._token_expires = time.time() + token_data.get("expired", 2592000)
        # Persist token
        TOKEN_FILE.write_text(json.dumps({
            "token": self._token,
            "expires": self._token_expires,
        }))
        try:
            TOKEN_FILE.chmod(0o600)
        except OSError:
            pass
        log.info("API token refreshed")
        return self._token

    async def _ensure_token(self) -> str:
        """Return valid token, refreshing if needed."""
        if self._token and self._token_expires > time.time() + 86400:
            return self._token
        async with self._lock:
            # Double-check after acquiring lock
            if self._token and self._token_expires > time.time() + 86400:
                return self._token
            return await self._sign_in()

    # ── Core HTTP ──────────────────────────────────────────────────────────

    async def _post(self, endpoint: str, data: dict | None = None, *, retry: bool = True) -> dict:
        """POST to api.kwork.ru with auth. Returns response dict."""
        token = await self._ensure_token()
        url = f"{API_BASE}/{endpoint}?token={token}"
        resp = await self._client.post(
            url,
            auth=BASIC_AUTH,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data or {},
        )
        body = resp.json()

        # Handle auth errors with one retry
        if not body.get("success") and retry:
            err = str(body.get("error", ""))
            code = body.get("error_code", 0)
            if code in (401, 403) or "авторизац" in err.lower() or "token" in err.lower():
                self._token = ""  # Force refresh
                return await self._post(endpoint, data, retry=False)

        if not body.get("success") and "error" in body:
            raise KworkApiError(f"/{endpoint}: {body.get('error', 'Unknown error')}")

        # Some endpoints return data directly (no {success, response} wrapper)
        return body.get("response", body)

    async def _post_raw(self, endpoint: str, data: dict | None = None) -> dict:
        """POST and return full response body (not just 'response' field)."""
        token = await self._ensure_token()
        url = f"{API_BASE}/{endpoint}?token={token}"
        resp = await self._client.post(
            url,
            auth=BASIC_AUTH,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data or {},
        )
        return resp.json()

    # ── Inbox / Dialogs ────────────────────────────────────────────────────

    async def get_dialogs(self) -> list[dict]:
        return await self._post("dialogs")

    async def get_dialog(self, user_id: int) -> dict:
        return await self._post("getDialog", {"id": str(user_id)})

    async def search_dialogs(self, query: str) -> list[dict]:
        return await self._post("searchDialogs", {"query": query})

    async def get_messages(self, username: str) -> list[dict]:
        """Get messages in a dialog by username."""
        return await self._post("inboxes", {"username": username})

    async def get_inbox_tracks(self, username: str) -> list[dict]:
        """Alternative message endpoint — returns up to 50 items."""
        return await self._post("getInboxTracks", {"username": username})

    async def send_message(self, user_id: int, text: str) -> dict:
        """Send a message to a user."""
        return await self._post("inboxCreate", {"user_id": str(user_id), "text": text})

    async def mark_read(self, username: str) -> dict:
        return await self._post("inboxRead", {"username": username})

    # ── Orders ─────────────────────────────────────────────────────────────

    async def get_orders(self, filter: str = "active") -> dict:
        """Get orders. filter: active/completed/cancelled/delivered/all."""
        return await self._post("workerOrders", {"filter": filter})

    async def get_order(self, order_id: int) -> dict:
        return await self._post("order", {"id": str(order_id)})

    async def get_order_header(self, order_id: int) -> dict:
        return await self._post("getOrderHeader", {"orderId": str(order_id)})

    async def get_order_details(self, order_id: int) -> dict:
        return await self._post("getOrderDetails", {"orderId": str(order_id)})

    async def get_order_tracks(self, order_id: int) -> list[dict]:
        """Get order chat messages."""
        resp = await self._post("getTracks", {"orderId": str(order_id)})
        if isinstance(resp, dict):
            return resp.get("messages", resp.get("tracks", []))
        return resp

    async def get_order_files(self, order_id: int) -> list[dict]:
        return await self._post("getOrderFiles", {"id": str(order_id)})

    async def send_order_message(self, order_id: int, user_id: int, text: str) -> dict:
        """Send a message in order chat via inboxCreate + orderId."""
        return await self._post("inboxCreate", {
            "user_id": str(user_id),
            "text": text,
            "orderId": str(order_id),
        })

    async def deliver_order(self, order_id: int) -> dict:
        return await self._post("sendOrderForApproval", {"orderId": str(order_id)})

    # ── Exchange ───────────────────────────────────────────────────────────

    async def get_projects(self, category: str = "", page: int = 1, query: str = "") -> list[dict]:
        data = {}
        if category:
            data["c"] = category
        if page > 1:
            data["page"] = str(page)
        if query:
            data["query"] = query
        return await self._post("projects", data)

    async def get_project(self, project_id: int) -> dict:
        return await self._post("project", {"id": str(project_id)})

    async def get_offer(self, offer_id: int) -> dict:
        """Get single offer details."""
        return await self._post("offer", {"id": str(offer_id)})

    async def get_my_proposals(self) -> list[dict]:
        return await self._post("offers")

    async def delete_proposal(self, offer_id: int) -> dict:
        return await self._post("deleteOffer", {"id": str(offer_id)})

    # ── Kworks ─────────────────────────────────────────────────────────────

    async def get_user_id(self) -> int:
        """Get current user ID (cached)."""
        if self._user_id:
            return self._user_id
        actor = await self.get_actor()
        self._user_id = actor.get("id")
        return self._user_id

    async def get_my_kworks(self) -> list[dict]:
        uid = await self.get_user_id()
        return await self._post("userKworks", {"user_id": str(uid)})

    async def pause_kwork(self, kwork_id: int) -> dict:
        return await self._post("pauseKwork", {"kwork_id": str(kwork_id)})

    async def start_kwork(self, kwork_id: int) -> dict:
        return await self._post("startKwork", {"kwork_id": str(kwork_id)})

    # ── Stats ──────────────────────────────────────────────────────────────

    async def get_actor(self) -> dict:
        return await self._post("actor")

    async def get_connects(self) -> dict:
        """Get connects info: {all_connects, active_connects, update_time}."""
        body = await self._post_raw("projects", {})
        return body.get("connects", {})

    async def get_exchange_info(self) -> dict:
        return await self._post("exchangeInfo")

    async def get_reviews(self) -> list[dict]:
        try:
            return await self._post("userReviews", {"user_id": str(await self.get_user_id())})
        except KworkApiError:
            return []

    async def get_payment_methods(self) -> dict:
        return await self._post("getPaymentMethods")
