"""Kwork Playwright client — fallback for operations without API."""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

log = logging.getLogger("kwork-mcp")

KWORK_BASE = "https://kwork.ru"
COOKIES_FILE = Path(__file__).parent / "cookies.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── JS Snippets ────────────────────────────────────────────────────────────

JS_INJECT_MESSAGE = r"""([html, raw]) => {
    const editor = document.querySelector('.trumbowyg-editor');
    const textarea = document.querySelector(
        '#message_body, #message_body1, textarea[name="message"]'
    );
    if (editor) {
        editor.innerHTML = html;
        ['input', 'change', 'keyup', 'keydown'].forEach(evt =>
            editor.dispatchEvent(new Event(evt, {bubbles: true}))
        );
    }
    if (textarea) textarea.value = raw;
}"""


class KworkBrowser:
    """Playwright-based Kwork client for operations without API."""

    def __init__(self):
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def connect(self):
        """Validate cookies exist (browser launches lazily)."""
        if not COOKIES_FILE.exists():
            log.warning(f"cookies.json not found at {COOKIES_FILE} — browser features limited")

    async def close(self):
        """Cleanup browser resources."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None
        self._initialized = False

    # ── Private ────────────────────────────────────────────────────────────

    async def _ensure_browser(self):
        """Launch browser if not running. Must be called inside _lock."""
        if self._initialized and self._page and not self._page.is_closed():
            return

        # Clean up stale state
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 1200},
            locale="ru-RU",
        )

        # Load cookies
        if COOKIES_FILE.exists():
            cookies = json.loads(COOKIES_FILE.read_text())
            for c in cookies:
                if c.get("domain") == "kwork.ru":
                    c["domain"] = ".kwork.ru"
            await self._context.add_cookies(cookies)
            log.info(f"Browser: loaded {len(cookies)} cookies")

        self._page = await self._context.new_page()
        self._initialized = True

    async def _navigate(self, path: str, wait_ms: int = 3000) -> Page:
        """Navigate to a Kwork page. Must be called inside _lock."""
        url = path if path.startswith("http") else f"{KWORK_BASE}{path}"
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning(f"Navigation error: {e}")
        await self._page.wait_for_timeout(wait_ms)

        # Check if redirected to login
        if "login" in self._page.url.lower():
            log.error("Browser session expired — redirected to login")
            raise RuntimeError("Cookies expired — browser redirected to login page")

        return self._page

    async def _inject_message(self, page: Page, text: str) -> bool:
        """Send message via Trumbowyg editor + submit. Must be inside _lock."""
        editor = page.locator(".trumbowyg-editor")
        if not await editor.count():
            log.error("Trumbowyg editor not found on page")
            return False

        await editor.first.click()
        await page.wait_for_timeout(300)

        html_text = text.replace("\n", "<br>")
        await page.evaluate(JS_INJECT_MESSAGE, [html_text, text])
        await page.wait_for_timeout(1000)

        # Try clicking submit button
        submit = page.locator("form button[type='submit']")
        try:
            await submit.first.wait_for(state="visible", timeout=5000)
            await submit.first.click()
            await page.wait_for_timeout(2000)
            return True
        except Exception:
            pass

        # Fallback: Ctrl+Enter
        await editor.first.press("Control+Enter")
        await page.wait_for_timeout(2000)
        log.info("Message sent via Ctrl+Enter fallback")
        return True

    # ── Public Operations ──────────────────────────────────────────────────

    async def send_order_message(self, order_id: str, text: str) -> bool:
        """Send a message in order chat via Playwright."""
        async with self._lock:
            log.info(f"Browser: sending message in order #{order_id}")
            await self._ensure_browser()
            page = await self._navigate(f"/track?id={order_id}", wait_ms=4000)
            return await self._inject_message(page, text)

    async def take_screenshot(self, path: str) -> str:
        """Take screenshot of a Kwork page. Returns saved file path."""
        async with self._lock:
            log.info(f"Browser: screenshot of {path}")
            await self._ensure_browser()
            page = await self._navigate(path, wait_ms=5000)
            save_path = f"/tmp/kwork_screenshot_{int(time.time())}.png"
            await page.screenshot(path=save_path, full_page=True)
            log.info(f"Screenshot saved: {save_path}")
            return save_path

    async def send_report(self, order_id: str, text: str) -> bool:
        """Send report/message in order via sendReport API from browser context."""
        async with self._lock:
            log.info(f"Browser: sending report in order #{order_id}")
            await self._ensure_browser()
            page = await self._navigate(f"/track?id={order_id}", wait_ms=4000)

            # Try using /sendmessage API from browser context (bypasses CSRF)
            csrf = ""
            for cookie in await self._context.cookies():
                if cookie["name"] == "csrf_user_token":
                    csrf = cookie["value"]
                    break

            if not csrf:
                # Fallback to editor injection
                return await self._inject_message(page, text)

            # Extract companion user ID from the page
            user_id = await page.evaluate("""() => {
                const input = document.querySelector('input[name="msgto"]');
                if (input) return input.value;
                const match = document.body.innerHTML.match(/"companion_id"\\s*:\\s*(\\d+)/);
                return match ? match[1] : '';
            }""")

            if not user_id:
                return await self._inject_message(page, text)

            result = await page.evaluate("""async (args) => {
                const formData = new FormData();
                formData.append('csrftoken', args.csrf);
                formData.append('message_body', args.message);
                formData.append('submg', '1');
                formData.append('msgto', args.user_id);
                formData.append('want_id', '');
                formData.append('message_id', '');
                formData.append('kworkId', '');
                formData.append('message_message_format', '');
                const resp = await fetch('/sendmessage', {
                    method: 'POST',
                    body: formData,
                    credentials: 'same-origin',
                    headers: {'X-Requested-With': 'XMLHttpRequest'}
                });
                const data = await resp.json();
                return {ok: !!data.MID, mid: data.MID || null, error: data.message || ''};
            }""", {"csrf": csrf, "message": text, "user_id": user_id})

            if result.get("ok"):
                log.info(f"Report sent via /sendmessage API (MID: {result['mid']})")
                return True

            log.warning(f"sendmessage failed: {result.get('error')}, falling back to editor")
            return await self._inject_message(page, text)

    async def submit_proposal(self, project_id: int, text: str, price: int, deadline: int) -> bool:
        """Submit a proposal to a project via browser (API doesn't support creation)."""
        async with self._lock:
            log.info(f"Browser: submitting proposal to project #{project_id}")
            await self._ensure_browser()
            page = await self._navigate(f"/projects/{project_id}/view", wait_ms=5000)

            # Fill the proposal form
            # Comment/description field
            editor = page.locator(".trumbowyg-editor")
            if await editor.count():
                await editor.first.click()
                html_text = text.replace("\n", "<br>")
                await page.evaluate(JS_INJECT_MESSAGE, [html_text, text])
            else:
                textarea = page.locator("textarea[name='comment'], textarea[name='description'], textarea.js-offer-comment")
                if await textarea.count():
                    await textarea.first.fill(text)

            # Price field
            price_input = page.locator("input[name='price'], input.js-offer-price")
            if await price_input.count():
                await price_input.first.fill(str(price))

            # Deadline field
            deadline_input = page.locator("input[name='duration'], input[name='deadline'], input.js-offer-duration")
            if await deadline_input.count():
                await deadline_input.first.fill(str(deadline))

            await page.wait_for_timeout(500)

            # Submit
            submit_btn = page.locator("button.js-offer-submit, button[type='submit'].js-send-offer, .js-offer-form button[type='submit']")
            if await submit_btn.count():
                await submit_btn.first.click()
                await page.wait_for_timeout(3000)
                log.info(f"Proposal submitted to project #{project_id}")
                return True

            # Fallback: try any submit button in the offer form
            form_submit = page.locator("form.js-offer-form button[type='submit'], .offer-form button[type='submit']")
            if await form_submit.count():
                await form_submit.first.click()
                await page.wait_for_timeout(3000)
                return True

            raise RuntimeError("Не удалось найти форму подачи предложения на странице")
