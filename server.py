#!/usr/bin/env python3
"""Kwork MCP Server — полный пульт управления Kwork через API + Playwright fallback.

14 инструментов: inbox, dialogs, orders, exchange, kworks, stats, screenshots.
Основной транспорт: HTTP API (api.kwork.ru). Fallback: Playwright для операций без API.
"""
import asyncio
import html
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from mcp.server.fastmcp import FastMCP, Context

from kwork_api import KworkApi, KworkError, KworkAuthError, KworkApiError
from kwork_browser import KworkBrowser

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [kwork-mcp] %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kwork-mcp")

# ── Lifespan ───────────────────────────────────────────────────────────────


@dataclass
class AppState:
    api: KworkApi
    browser: KworkBrowser


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncGenerator[AppState, None]:
    api = KworkApi()
    browser = KworkBrowser()
    await api.connect()
    await browser.connect()
    log.info("Kwork MCP server started")
    try:
        yield AppState(api=api, browser=browser)
    finally:
        await api.close()
        await browser.close()
        log.info("Kwork MCP server stopped")


mcp = FastMCP("kwork", lifespan=lifespan, dependencies=["httpx", "playwright"])

# ── Helpers ────────────────────────────────────────────────────────────────


def _state(ctx: Context) -> AppState:
    return ctx.request_context.lifespan_context


def _ts(unix: int | float | str) -> str:
    """Format unix timestamp to human-readable."""
    try:
        ts = int(unix) if unix else 0
        if ts == 0:
            return ""
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        elif dt.year == now.year:
            return dt.strftime("%d %b %H:%M")
        return dt.strftime("%d %b %Y %H:%M")
    except (ValueError, TypeError, OSError):
        return str(unix)


def _clean(text: str) -> str:
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = html.unescape(text)
    # Strip simple HTML tags
    import re
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


# ── Tool 1: kwork_inbox ───────────────────────────────────────────────────


@mcp.tool()
async def kwork_inbox(ctx: Context, filter: str = "all") -> str:
    """Список диалогов в инбоксе. filter: 'all' (все) или 'unread' (непрочитанные)."""
    try:
        state = _state(ctx)
        dialogs = await state.api.get_dialogs()
        if not isinstance(dialogs, list):
            return "Нет диалогов."

        if filter == "unread":
            dialogs = [d for d in dialogs if d.get("unread_count", 0) > 0 or d.get("unread")]

        if not dialogs:
            return "Нет непрочитанных диалогов." if filter == "unread" else "Нет диалогов."

        lines = []
        for d in dialogs:
            name = d.get("username", "?")
            uid = d.get("user_id", "")
            unread = d.get("unread_count", 0)
            last_msg = _clean(d.get("last_message", ""))
            time_str = _ts(d.get("time", ""))
            online = "🟢" if d.get("is_online") else ""
            has_order = " [заказ]" if d.get("has_active_order") else ""
            unread_str = f" ({unread} непрочит.)" if unread else ""

            lines.append(f"{online}{name} (id:{uid}){unread_str}{has_order} {time_str}")
            if last_msg:
                preview = last_msg[:120].replace("\n", " ")
                lines.append(f"  └ {preview}")
        return "\n".join(lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 2: kwork_dialog ──────────────────────────────────────────────────


@mcp.tool()
async def kwork_dialog(ctx: Context, user: str, limit: int = 20) -> str:
    """Полный диалог с пользователем. user: username или user_id."""
    try:
        state = _state(ctx)

        # Get dialog info
        dialogs = await state.api.get_dialogs()
        dialog_info = None
        username = user
        if isinstance(dialogs, list):
            for d in dialogs:
                if str(d.get("user_id", "")) == user or d.get("username", "").lower() == user.lower():
                    dialog_info = d
                    username = d.get("username", user)
                    break

        # Get messages
        messages = await state.api.get_messages(username)
        if not isinstance(messages, list) or not messages:
            # Fallback
            messages = await state.api.get_inbox_tracks(username)

        if not isinstance(messages, list) or not messages:
            return f"Нет сообщений с {user}."

        # Build header
        header_parts = [f"=== Диалог: {username} ==="]
        if dialog_info:
            uid = dialog_info.get("user_id", "")
            online = "онлайн" if dialog_info.get("is_online") else "офлайн"
            header_parts.append(f"ID: {uid} | {online}")
            if dialog_info.get("has_active_order"):
                orders = dialog_info.get("active_orders", [])
                if orders:
                    order_info = ", ".join(f"#{o.get('id', '?')}" for o in orders[:3])
                    header_parts.append(f"Активные заказы: {order_info}")
                else:
                    header_parts.append("Есть активный заказ")

        header_parts.append(f"Сообщений: {len(messages)} | Показано: {min(limit, len(messages))}")
        header_parts.append("─" * 50)

        # Format messages (last N)
        msg_lines = []
        for m in messages[-limit:]:
            sender = m.get("from_username", m.get("sender", "?"))
            text = _clean(m.get("message", m.get("text", "")))
            time_str = _ts(m.get("time", m.get("sent_timestamp", "")))
            msg_type = m.get("type", "text")

            # Determine if it's our message
            is_me = sender == "alex_claw"
            prefix = "Я" if is_me else sender

            # System messages
            if msg_type in ("offer_kwork_new", "offer_kwork_done", "order_created",
                           "order_completed", "order_cancelled"):
                type_labels = {
                    "offer_kwork_new": "Предложение кворка",
                    "offer_kwork_done": "Заказ завершён",
                    "order_created": "Заказ создан",
                    "order_completed": "Заказ выполнен",
                    "order_cancelled": "Заказ отменён",
                }
                label = type_labels.get(msg_type, msg_type)
                msg_lines.append(f"[{time_str}] ⚙ {label}")
                if text:
                    msg_lines.append(f"         {text[:200]}")
            else:
                msg_lines.append(f"[{time_str}] {prefix}: {text[:500]}")

        return "\n".join(header_parts + msg_lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 3: kwork_send ────────────────────────────────────────────────────


@mcp.tool()
async def kwork_send(ctx: Context, user_id: int, text: str) -> str:
    """Отправить сообщение пользователю. user_id: числовой ID."""
    try:
        state = _state(ctx)
        await state.api.send_message(user_id, text)
        return f"Сообщение отправлено (user_id: {user_id})."
    except KworkError as e:
        return f"Ошибка отправки: {e}"


# ── Tool 4: kwork_orders ──────────────────────────────────────────────────


@mcp.tool()
async def kwork_orders(ctx: Context, status: str = "active") -> str:
    """Список заказов. status: active/completed/cancelled/all."""
    try:
        state = _state(ctx)
        result = await state.api.get_orders(status)
        if isinstance(result, dict):
            orders = result.get("orders", [])
            counts = result.get("filter_counts", {})
        else:
            orders = result if isinstance(result, list) else []
            counts = {}

        if counts:
            summary = " | ".join(f"{k}: {v}" for k, v in counts.items())
            header = f"Заказы ({status}) — {summary}"
        else:
            header = f"Заказы ({status})"

        if not orders:
            return f"{header}\nНет заказов."

        lines = [header, "─" * 50]
        for o in orders:
            oid = o.get("id", "?")
            title = _clean(o.get("title", o.get("kwork_title", "?")))
            price = o.get("price", "?")
            status_val = o.get("status", "?")
            lines.append(f"#{oid} [{status_val}] {price}₽ — {title[:80]}")
        return "\n".join(lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 5: kwork_order ───────────────────────────────────────────────────


@mcp.tool()
async def kwork_order(ctx: Context, order_id: int) -> str:
    """Детали заказа + чат. Собирает данные из 3 API-вызовов."""
    try:
        state = _state(ctx)
        # Parallel requests
        header_task = state.api.get_order_header(order_id)
        details_task = state.api.get_order_details(order_id)
        tracks_task = state.api.get_order_tracks(order_id)
        header, details, tracks = await asyncio.gather(
            header_task, details_task, tracks_task,
            return_exceptions=True,
        )

        lines = [f"=== Заказ #{order_id} ==="]

        # Header info
        if isinstance(header, dict):
            order_info = header.get("order", {})
            kwork_info = header.get("kwork", {})
            payer_info = header.get("payer", {})
            title = _clean(order_info.get("title", ""))
            price = order_info.get("price", "?")
            lines.append(f"Название: {title}")
            lines.append(f"Цена: {price}₽")
            if payer_info:
                lines.append(f"Заказчик: {payer_info.get('username', '?')}")
            if kwork_info:
                lines.append(f"Кворк: {_clean(kwork_info.get('title', ''))}")
        elif isinstance(header, Exception):
            lines.append(f"⚠ Заголовок: {header}")

        # Details
        if isinstance(details, dict):
            desc = _clean(details.get("details", {}).get("description", ""))
            if desc:
                lines.append(f"\nОписание:\n{desc[:500]}")
            key_tracks = details.get("key_tracks", [])
            if key_tracks:
                lines.append("\nТаймлайн:")
                for kt in key_tracks:
                    lines.append(f"  {_ts(kt.get('created_at', ''))} — {kt.get('title', '?')}")
        elif isinstance(details, Exception):
            lines.append(f"⚠ Детали: {details}")

        # Chat messages
        if isinstance(tracks, list) and tracks:
            lines.append(f"\n{'─' * 50}")
            lines.append(f"Чат ({len(tracks)} сообщений, последние 15):")
            for m in tracks[-15:]:
                sender = m.get("from_name", "?")
                text = _clean(m.get("text", ""))
                time_str = _ts(m.get("sent_timestamp", ""))
                is_me = sender == "alex_claw"
                prefix = "Я" if is_me else sender
                lines.append(f"[{time_str}] {prefix}: {text[:300]}")
        elif isinstance(tracks, Exception):
            lines.append(f"⚠ Чат: {tracks}")

        return "\n".join(lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 6: kwork_order_message ────────────────────────────────────────────


@mcp.tool()
async def kwork_order_message(ctx: Context, order_id: int, text: str) -> str:
    """Отправить сообщение в чат заказа. Нужен order_id. Автоматически определяет user_id заказчика."""
    try:
        state = _state(ctx)
        # Get payer user_id from order header
        header = await state.api.get_order_header(order_id)
        payer = header.get("payer", {})
        user_id = payer.get("id")
        if not user_id:
            return f"Не удалось определить заказчика для заказа #{order_id}."
        await state.api.send_order_message(order_id, user_id, text)
        return f"Сообщение отправлено в заказ #{order_id} (заказчику {payer.get('username', user_id)})."
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 7: kwork_order_deliver ────────────────────────────────────────────


@mcp.tool()
async def kwork_order_deliver(ctx: Context, order_id: int, text: str = "") -> str:
    """Сдать заказ на проверку."""
    try:
        state = _state(ctx)
        result = await state.api.deliver_order(order_id)
        return f"Заказ #{order_id} сдан на проверку."
    except KworkError as e:
        return f"Ошибка доставки: {e}"


# ── Tool 8: kwork_exchange ─────────────────────────────────────────────────


@mcp.tool()
async def kwork_exchange(ctx: Context, category: str = "", page: int = 1, query: str = "") -> str:
    """Просмотр биржи проектов. Фильтры: category, page, query."""
    try:
        state = _state(ctx)
        projects = await state.api.get_projects(category, page, query)

        if isinstance(projects, dict):
            items = projects.get("wants", projects.get("projects", []))
        elif isinstance(projects, list):
            items = projects
        else:
            items = []

        if not items:
            return "Проектов не найдено."

        lines = [f"Биржа проектов (стр. {page})", "─" * 50]
        for p in items:
            pid = p.get("id", "?")
            title = _clean(p.get("title", ""))
            price = p.get("price", "?")
            desc = _clean(p.get("description", ""))
            offers = p.get("offers", 0)
            time_left = p.get("time_left", 0)
            hours_left = int(time_left) // 3600 if time_left else 0

            lines.append(f"#{pid} [{price}₽] {title[:100]}")
            if desc:
                lines.append(f"  {desc[:200]}")
            lines.append(f"  Откликов: {offers} | Осталось: {hours_left}ч")
        return "\n".join(lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 9: kwork_project ─────────────────────────────────────────────────


@mcp.tool()
async def kwork_project(ctx: Context, project_id: int) -> str:
    """Детали проекта на бирже."""
    try:
        state = _state(ctx)
        p = await state.api.get_project(project_id)

        lines = [
            f"=== Проект #{p.get('id', project_id)} ===",
            f"Название: {_clean(p.get('title', ''))}",
            f"Бюджет: {p.get('price', '?')}₽",
            f"Заказчик: {p.get('username', '?')} (проектов: {p.get('user_projects_count', '?')})",
            f"Категория: {p.get('category_id', '?')}",
            f"Статус: {p.get('status', '?')}",
            f"Откликов: {p.get('offers', 0)}",
        ]
        time_left = p.get("time_left", 0)
        if time_left:
            lines.append(f"Осталось: {int(time_left) // 3600}ч")

        desc = _clean(p.get("description", ""))
        if desc:
            lines.append(f"\nОписание:\n{desc}")

        return "\n".join(lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 10: kwork_propose ─────────────────────────────────────────────────


@mcp.tool()
async def kwork_propose(ctx: Context, project_id: int, text: str, price: int, deadline: int = 3) -> str:
    """Подать предложение на проект. deadline в днях. Использует браузер (API не поддерживает создание)."""
    try:
        state = _state(ctx)
        result = await state.browser.submit_proposal(project_id, text, price, deadline)
        return f"Предложение подано на проект #{project_id} ({price}₽, {deadline} дн.)."
    except Exception as e:
        return f"Ошибка: {e}"


# ── Tool 11: kwork_my_kworks ──────────────────────────────────────────────


@mcp.tool()
async def kwork_my_kworks(ctx: Context) -> str:
    """Список моих кворков."""
    try:
        state = _state(ctx)
        kworks = await state.api.get_my_kworks()
        if not isinstance(kworks, list) or not kworks:
            return "Нет кворков."

        lines = [f"Мои кворки ({len(kworks)})", "─" * 50]
        for kw in kworks:
            kid = kw.get("id", "?")
            title = _clean(kw.get("title", ""))
            status = kw.get("status", "?")
            price = kw.get("price", kw.get("min_price", "?"))
            lines.append(f"#{kid} [{status}] {price}₽ — {title[:80]}")
        return "\n".join(lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 12: kwork_kwork_toggle ────────────────────────────────────────────


@mcp.tool()
async def kwork_kwork_toggle(ctx: Context, kwork_id: int, active: bool) -> str:
    """Активировать (active=true) или поставить на паузу (active=false) кворк."""
    try:
        state = _state(ctx)
        if active:
            await state.api.start_kwork(kwork_id)
            return f"Кворк #{kwork_id} активирован."
        else:
            await state.api.pause_kwork(kwork_id)
            return f"Кворк #{kwork_id} поставлен на паузу."
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 13: kwork_stats ──────────────────────────────────────────────────


@mcp.tool()
async def kwork_stats(ctx: Context) -> str:
    """Статистика аккаунта: баланс, рейтинг, заказы, коннекты."""
    try:
        state = _state(ctx)
        actor = await state.api.get_actor()

        lines = [
            "=== Статистика Kwork ===",
            f"Пользователь: {actor.get('username', '?')} ({actor.get('fullname', '')})",
            f"Рейтинг: {actor.get('rating', '?')} ({actor.get('good_reviews', 0)} положит. / {actor.get('bad_reviews', 0)} отриц.)",
            f"Баланс: {actor.get('free_amount', 0)}₽ (в холде: {actor.get('hold_amount', 0)}₽)",
            f"Выполнено заказов: {actor.get('completed_orders_count', 0)}",
            f"Кворков: {actor.get('kworks_count', 0)}",
            f"Коннекты: {actor.get('offers_count', 0)}",
            f"Непрочитанных: {actor.get('unread_dialog_count', 0)} диалогов, {actor.get('unread_messages_count', 0)} сообщений",
            f"Статус: {actor.get('worker_status', '?')} | Аккаунт: {actor.get('status', '?')}",
            f"Специализация: {actor.get('specialization', '?')}",
        ]
        return "\n".join(lines)
    except KworkError as e:
        return f"Ошибка: {e}"


# ── Tool 14: kwork_screenshot ──────────────────────────────────────────────


@mcp.tool()
async def kwork_screenshot(ctx: Context, path: str) -> str:
    """Скриншот страницы Kwork. path: URL-путь, например '/inbox' или '/projects'."""
    try:
        state = _state(ctx)
        saved = await state.browser.take_screenshot(path)
        return f"Скриншот сохранён: {saved}"
    except Exception as e:
        return f"Ошибка скриншота: {e}"


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
