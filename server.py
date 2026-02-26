#!/usr/bin/env python3
"""Kwork MCP Server — unified interface for all Kwork operations.

Tools: inbox, orders, exchange, proposals, stats.
Backend: Playwright with persistent cookie session.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# ── Config ──────────────────────────────────────────────────────────────────

COOKIES_FILE = Path(__file__).parent / "cookies.json"
BASE_URL = "https://kwork.ru"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

logging.basicConfig(
    format="%(asctime)s [kwork-mcp] %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kwork-mcp")

# ── JS Snippets (separate to avoid escaping hell) ──────────────────────────

JS_INBOX_LIST = r"""() => {
    const items = document.querySelectorAll("li.chat__list-item");
    return [...items].map(item => {
        const nameEl = item.querySelector(".chat__list-user");
        const timeEl = item.querySelector(".chat__list-date");
        const msgEl = item.querySelector(".chat__list-message");
        const counterEl = item.querySelector(".chat__list-item-counter");
        const avatarImg = item.querySelector("img.user-avatar__picture");

        // Extract user ID from avatar URL: /avatar/medium/39/13731603-1.jpg
        let userId = "";
        if (avatarImg) {
            const src = avatarImg.getAttribute("src") || "";
            const m = src.match(/\/(\d+)-\d+\.\w+$/);
            if (m) userId = m[1];
        }

        // Check for unread counter
        const unread = counterEl ? parseInt(counterEl.textContent.trim()) || 0 : 0;
        const name = nameEl ? nameEl.textContent.trim() : "";

        return {
            name,
            time: timeEl ? timeEl.textContent.trim() : "",
            preview: msgEl ? msgEl.textContent.trim().substring(0, 150) : "",
            unread: unread,
            user_id: userId || name,
        };
    });
}"""

JS_CHAT_MESSAGES = r"""() => {
    // Inbox chat uses conversation-message-block inside .js-message-block
    const blocks = document.querySelectorAll(".js-message-block.conversation-message-block");
    if (blocks.length > 0) {
        return [...blocks].map(m => {
            const cls = m.getAttribute("class") || "";
            // Own messages: cm-my class or no avatar link
            const avatarLink = m.querySelector(".cm-avatar a[title]");
            const usernameEl = m.querySelector(".username-c a, .username-c");
            const textEl = m.querySelector(".cm-message-html");
            const timeEl = m.querySelector(".cm-message-time, [class*='message-time']");

            // Determine sender
            let sender = "them";
            if (cls.includes("cm-my") || cls.includes(" out")) {
                sender = "me";
            } else if (avatarLink) {
                const title = avatarLink.getAttribute("title") || "";
                sender = title || "them";
            } else if (usernameEl) {
                sender = usernameEl.textContent.trim().split(/[\n\t]/)[0].trim();
            }

            return {
                sender,
                text: textEl ? textEl.textContent.trim() : "",
                time: timeEl ? timeEl.textContent.trim() : "",
            };
        }).filter(m => m.text.length > 1);
    }

    // Fallback for non-conversation pages
    const msgs = document.querySelectorAll(".js-message-block");
    return [...msgs].map(m => {
        const cls = m.getAttribute("class") || "";
        const isOwn = cls.includes(" out");
        const titleEl = m.querySelector(".track--item__title");
        const contentEl = m.querySelector(".track--item__content");
        const timeEl = m.querySelector(".track--item__sidebar-time");
        let sender = "them";
        if (isOwn) sender = "me";
        else if (titleEl) sender = titleEl.textContent.trim().split(/[\n\t]/)[0].trim();
        return {
            sender,
            text: contentEl ? contentEl.textContent.trim() : "",
            time: timeEl ? timeEl.textContent.trim() : "",
        };
    }).filter(m => m.text.length > 1);
}"""

JS_ORDER_DATA = r"""() => {
    const h1 = document.querySelector("h1");
    const title = h1 ? h1.textContent.trim() : "";

    // Order info table
    const infoText = document.body.innerText;
    const orderMatch = infoText.match(/Заказ\s*[№#](\d+)/);
    const orderId = orderMatch ? orderMatch[1] : "";

    // Messages in order chat (Kwork uses .js-message-block)
    const msgs = document.querySelectorAll(".js-message-block");
    const messages = [...msgs].map(m => {
        const cls = m.getAttribute("class") || "";
        const isOwn = cls.includes(" out");
        const titleEl = m.querySelector(".track--item__title");
        const contentEl = m.querySelector(".track--item__content");
        const timeEl = m.querySelector(".track--item__sidebar-time");

        // Extract username from title (first line before newline/tab)
        let sender = "client";
        if (isOwn) {
            sender = "me";
        } else if (titleEl) {
            sender = titleEl.textContent.trim().split(/[\n\t]/)[0].trim();
        }

        return {
            sender,
            text: contentEl ? contentEl.textContent.trim() : "",
            time: timeEl ? timeEl.textContent.trim() : "",
        };
    }).filter(m => m.text.length > 1);

    return {
        title,
        status: title,
        deadline: "",
        messages,
    };
}"""

JS_EXCHANGE = r"""() => {
    const items = document.querySelectorAll(".wants-card, [class*='want-card'], [class*='project-item']");
    return [...items].map(item => {
        const titleEl = item.querySelector("a[href*='/projects/']");
        const priceEl = item.querySelector("[class*='price'], [class*='budget']");
        const descEl = item.querySelector("[class*='description'], [class*='text'], [class*='want-card__description']");
        const proposalsEl = item.querySelector("[class*='offers'], [class*='proposals']");
        const href = titleEl ? titleEl.getAttribute("href") : "";
        const idMatch = href ? href.match(/\/(\d+)/) : null;

        return {
            id: idMatch ? idMatch[1] : "",
            title: titleEl ? titleEl.textContent.trim().substring(0, 120) : "",
            price: priceEl ? priceEl.textContent.replace(/\s+/g, " ").trim() : "",
            description: descEl ? descEl.textContent.trim().substring(0, 300) : "",
            proposals: proposalsEl ? proposalsEl.textContent.replace(/\s+/g, " ").trim() : "",
            href: href,
        };
    }).filter(p => p.title);
}"""

JS_CONNECTS = r"""() => {
    const el = document.querySelector(".connects-points__connects-count");
    if (el) {
        const text = el.textContent.trim();
        const m = text.match(/(\d+)\s*из\s*(\d+)/);
        if (m) return {remaining: parseInt(m[1]), total: parseInt(m[2])};
    }
    // Fallback: body text
    const body = document.body.textContent;
    const m = body.match(/(\d+)\s*из\s*30/);
    if (m) return {remaining: parseInt(m[1]), total: 30};
    return {remaining: null, total: null};
}"""

JS_BALANCE = r"""() => {
    // Balance page has it as a big number
    const text = document.body.innerText;
    const m = text.match(/Баланс\s*\n?\s*([\d\s]+)\s*₽/);
    if (m) return m[1].replace(/\s/g, "") + " ₽";
    // Fallback: header balance link
    const link = document.querySelector("a[href='/balance']");
    if (link) return link.textContent.replace(/\s+/g, " ").trim();
    return null;
}"""

# ── MCP Server ──────────────────────────────────────────────────────────────

mcp = FastMCP("kwork", dependencies=["playwright"])

# ── Kwork Client ────────────────────────────────────────────────────────────


class KworkClient:
    """Playwright-based Kwork client with persistent browser session."""

    def __init__(self):
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def _ensure_browser(self):
        """Launch browser if not running, load cookies."""
        if self._page and not self._page.is_closed():
            return

        if not self._pw:
            self._pw = await async_playwright().start()

        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=USER_AGENT)

        if COOKIES_FILE.exists():
            cookies = json.loads(COOKIES_FILE.read_text())
            for c in cookies:
                if c.get("domain") == "kwork.ru":
                    c["domain"] = ".kwork.ru"
            await self._context.add_cookies(cookies)
            log.info(f"Loaded {len(cookies)} cookies")

        self._page = await self._context.new_page()

    async def _go(self, path: str, wait_ms: int = 3000) -> Page:
        """Navigate to a Kwork page."""
        await self._ensure_browser()
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        await self._page.wait_for_timeout(wait_ms)
        return self._page

    async def _send_msg(self, page: Page, text: str) -> bool:
        """Send a message using Trumbowyg editor + form submit."""
        editor = page.locator(".trumbowyg-editor")
        if not await editor.count():
            return False

        # Click editor to focus it (Kwork JS watches for focus)
        await editor.first.click()
        await page.wait_for_timeout(300)

        html_text = text.replace("\n", "<br>")
        await page.evaluate(
            """([html, raw]) => {
                const editor = document.querySelector('.trumbowyg-editor');
                const textarea = document.querySelector('#message_body, #message_body1, textarea[name="message"]');
                if (editor) {
                    editor.innerHTML = html;
                    // Fire all events Kwork JS listens for
                    ['input', 'change', 'keyup', 'keydown'].forEach(evt =>
                        editor.dispatchEvent(new Event(evt, {bubbles: true}))
                    );
                }
                if (textarea) textarea.value = raw;
            }""",
            [html_text, text],
        )
        # Wait for Kwork to enable the send button
        await page.wait_for_timeout(1000)

        submit = page.locator("form button[type='submit']")
        try:
            await submit.first.wait_for(state="visible", timeout=5000)
            await submit.first.click()
            await page.wait_for_timeout(2000)
            return True
        except Exception:
            pass

        # Fallback: try Ctrl+Enter
        await editor.press("Control+Enter")
        await page.wait_for_timeout(2000)
        return True

    # ── Inbox ───────────────────────────────────────────────────────────────

    async def inbox_list(self) -> list[dict]:
        """List all inbox conversations."""
        page = await self._go("/inbox", wait_ms=5000)
        return await page.evaluate(JS_INBOX_LIST)

    async def inbox_read(self, user_id: str) -> list[dict]:
        """Read conversation with a user. Click their chat to load messages."""
        page = await self._go("/inbox", wait_ms=5000)

        # Click correct chat item
        items = page.locator("li.chat__list-item")
        count = await items.count()
        for i in range(count):
            item = items.nth(i)
            # Check avatar img for user ID
            html = await item.inner_html()
            if user_id in html:
                await item.click()
                await page.wait_for_timeout(3000)
                break
            # Also check by name
            name_el = item.locator(".chat__list-user")
            name = (await name_el.text_content()).strip() if await name_el.count() else ""
            if name and user_id.lower() in name.lower():
                await item.click()
                await page.wait_for_timeout(3000)
                break

        return await page.evaluate(JS_CHAT_MESSAGES)

    async def inbox_send(self, user_id: str, text: str) -> bool:
        """Send a message to a user in inbox."""
        # First load the chat
        await self.inbox_read(user_id)
        return await self._send_msg(self._page, text)

    # ── Orders ──────────────────────────────────────────────────────────────

    async def order_list(self) -> list[dict]:
        """List active orders."""
        page = await self._go("/manage_orders", wait_ms=5000)
        return await page.evaluate(r"""() => {
            const rows = document.querySelectorAll("tr.track-item, [class*='order-row'], a[href*='/track?id=']");
            const orders = [];
            const seen = new Set();
            const allLinks = document.querySelectorAll("a[href*='/track?id=']");
            for (const a of allLinks) {
                const href = a.getAttribute("href") || "";
                const m = href.match(/id=(\d+)/);
                if (!m || seen.has(m[1])) continue;
                seen.add(m[1]);
                orders.push({
                    id: m[1],
                    title: a.textContent.trim().substring(0, 100),
                    href: href,
                });
            }
            return orders;
        }""")

    async def order_read(self, order_id: str) -> dict:
        """Read order details and chat."""
        page = await self._go(f"/track?id={order_id}", wait_ms=5000)
        data = await page.evaluate(JS_ORDER_DATA)
        data["order_id"] = order_id
        return data

    async def order_send(self, order_id: str, text: str) -> bool:
        """Send a message in order chat."""
        page = await self._go(f"/track?id={order_id}", wait_ms=5000)
        return await self._send_msg(page, text)

    # ── Exchange ────────────────────────────────────────────────────────────

    async def exchange_browse(self, category: str = "", page_num: int = 1) -> list[dict]:
        """Browse project exchange."""
        url = "/projects"
        params = []
        if category:
            params.append(f"c={category}")
        if page_num > 1:
            params.append(f"page={page_num}")
        if params:
            url += "?" + "&".join(params)

        page = await self._go(url, wait_ms=5000)
        return await page.evaluate(JS_EXCHANGE)

    # ── Stats ───────────────────────────────────────────────────────────────

    async def stats(self) -> dict:
        """Get account stats: connects from /projects, balance from /balance."""
        # Connects from exchange page
        page = await self._go("/projects", wait_ms=5000)
        connects = await page.evaluate(JS_CONNECTS)

        # Balance from balance page
        page = await self._go("/balance", wait_ms=3000)
        balance = await page.evaluate(JS_BALANCE)

        return {
            "connects_remaining": connects.get("remaining"),
            "connects_total": connects.get("total"),
            "balance": balance,
        }

    async def close(self):
        """Cleanup browser."""
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()


# ── Global client ───────────────────────────────────────────────────────────

client = KworkClient()

# ── MCP Tools ───────────────────────────────────────────────────────────────


@mcp.tool()
async def kwork_inbox_list() -> str:
    """List all inbox conversations with previews, times, and unread counts."""
    chats = await client.inbox_list()
    if not chats:
        return "No conversations found."
    lines = []
    for c in chats:
        unread = f" [{c['unread']} unread]" if c.get("unread") else ""
        uid = f" (uid:{c['user_id']})" if c.get("user_id") else ""
        lines.append(f"{c['name']}{uid}{unread} ({c['time']})")
        if c.get("preview"):
            lines.append(f"  {c['preview'][:100]}")
    return "\n".join(lines)


@mcp.tool()
async def kwork_inbox_read(user_id: str) -> str:
    """Read full conversation with a user. Pass user_id (numeric) or username."""
    msgs = await client.inbox_read(user_id)
    if not msgs:
        return f"No messages found for {user_id}."
    lines = []
    for m in msgs:
        prefix = "ME" if m["sender"] == "me" else m["sender"]
        lines.append(f"[{m['time']}] {prefix}: {m['text']}")
    return "\n".join(lines)


@mcp.tool()
async def kwork_inbox_send(user_id: str, text: str) -> str:
    """Send a message to a user in inbox chat. Pass user_id or username."""
    ok = await client.inbox_send(user_id, text)
    return "Message sent." if ok else "Failed to send message."


@mcp.tool()
async def kwork_order_list() -> str:
    """List active orders with IDs and titles."""
    orders = await client.order_list()
    if not orders:
        return "No active orders."
    return "\n".join(f"#{o['id']} — {o['title']}" for o in orders)


@mcp.tool()
async def kwork_order_read(order_id: str) -> str:
    """Read order details and chat messages by order ID."""
    data = await client.order_read(order_id)
    lines = [f"Order #{data['order_id']}: {data.get('title', '')}"]
    if data.get("status"):
        lines.append(f"Status: {data['status']}")
    if data.get("deadline"):
        lines.append(f"Deadline: {data['deadline']}")
    lines.append("---")
    for m in data.get("messages", []):
        prefix = "ME" if m["sender"] == "me" else "CLIENT"
        lines.append(f"[{m['time']}] {prefix}: {m['text']}")
    return "\n".join(lines)


@mcp.tool()
async def kwork_order_send(order_id: str, text: str) -> str:
    """Send a message in order chat by order ID."""
    ok = await client.order_send(order_id, text)
    return "Message sent." if ok else "Failed to send message."


@mcp.tool()
async def kwork_exchange_browse(category: str = "", page_num: int = 1) -> str:
    """Browse project exchange. Optional category and page number."""
    projects = await client.exchange_browse(category, page_num)
    if not projects:
        return "No projects found."
    lines = []
    for p in projects:
        lines.append(f"#{p['id']} [{p['price']}] {p['title']}")
        if p.get("description"):
            lines.append(f"  {p['description'][:150]}")
    return "\n".join(lines)


@mcp.tool()
async def kwork_stats() -> str:
    """Get account stats: connects remaining, balance."""
    data = await client.stats()
    parts = ["Kwork Stats:"]
    if data.get("connects_remaining") is not None:
        parts.append(f"Connects: {data['connects_remaining']}/{data.get('connects_total', 30)}")
    if data.get("balance"):
        parts.append(f"Balance: {data['balance']}")
    return "\n".join(parts)


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
