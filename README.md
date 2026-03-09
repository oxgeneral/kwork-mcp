# kwork-mcp

MCP-сервер для [Kwork.ru](https://kwork.ru) — российской фриланс-платформы. 14 инструментов для полного управления аккаунтом через [Model Context Protocol](https://modelcontextprotocol.io).

Основной транспорт — **HTTP API** (`api.kwork.ru`). Playwright используется как fallback для операций без API (подача предложений, скриншоты).

## Инструменты

| # | Инструмент | Описание | Транспорт |
|---|-----------|----------|-----------|
| 1 | `kwork_inbox` | Список диалогов (все / непрочитанные) | API |
| 2 | `kwork_dialog` | Полный диалог с пользователем | API |
| 3 | `kwork_send` | Отправить сообщение пользователю | API |
| 4 | `kwork_orders` | Список заказов (active/completed/cancelled) | API |
| 5 | `kwork_order` | Детали заказа + чат (3 параллельных вызова) | API |
| 6 | `kwork_order_message` | Сообщение в чат заказа | API |
| 7 | `kwork_order_deliver` | Сдать заказ на проверку | API |
| 8 | `kwork_exchange` | Биржа проектов (фильтры, поиск, пагинация) | API |
| 9 | `kwork_project` | Детали проекта на бирже | API |
| 10 | `kwork_propose` | Подать предложение на проект | Playwright |
| 11 | `kwork_my_kworks` | Список моих кворков | API |
| 12 | `kwork_kwork_toggle` | Пауза / активация кворка | API |
| 13 | `kwork_stats` | Статистика: баланс, рейтинг, заказы | API |
| 14 | `kwork_screenshot` | Скриншот любой страницы Kwork | Playwright |

## Установка

```bash
git clone https://github.com/your-username/kwork-mcp.git
cd kwork-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Настройка

Создайте файл `.env` в корне проекта:

```env
KWORK_LOGIN=your_email@example.com
KWORK_PASSWORD=your_password
```

Для Playwright-инструментов (скриншоты, подача предложений) экспортируйте cookies из браузера в `cookies.json`:

```json
[
  {"name": "PHPSESSID", "value": "...", "domain": "kwork.ru", "path": "/"},
  {"name": "csrf_user_token", "value": "...", "domain": "kwork.ru", "path": "/"}
]
```

> Экспортировать cookies можно через расширения [Cookie-Editor](https://cookie-editor.cgagnier.ca/) или [EditThisCookie](https://www.editthiscookie.com/).

## Использование

### Claude Desktop

Добавьте в `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kwork": {
      "command": "/path/to/kwork-mcp/.venv/bin/python3",
      "args": ["/path/to/kwork-mcp/server.py"]
    }
  }
}
```

### Claude Code

Добавьте в `.mcp.json` проекта:

```json
{
  "mcpServers": {
    "kwork": {
      "command": "/path/to/kwork-mcp/.venv/bin/python3",
      "args": ["/path/to/kwork-mcp/server.py"]
    }
  }
}
```

### Standalone

```bash
python3 server.py
```

Сервер работает по протоколу MCP через stdio.

## Архитектура

```
server.py          — FastMCP сервер, 14 инструментов
kwork_api.py       — HTTP-клиент api.kwork.ru (авторизация, кеш токена)
kwork_browser.py   — Playwright fallback (headless Chromium)
```

- **API-клиент** автоматически получает и кеширует токен (`.kwork_token.json`), обновляет при истечении
- **Playwright** запускается лениво — только при первом вызове browser-инструмента
- `kwork_order` делает 3 параллельных API-вызова через `asyncio.gather`
- `kwork_order_message` автоматически определяет `user_id` заказчика

## Лицензия

MIT
