<p align="center">
  <h1 align="center">kwork-mcp</h1>
  <p align="center">
    <strong>AI-управление фрилансом на Kwork.ru через Model Context Protocol</strong>
  </p>
  <p align="center">
    <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-Compatible-blue?style=flat-square" alt="MCP Compatible"></a>
    <a href="https://kwork.ru"><img src="https://img.shields.io/badge/Kwork.ru-API-green?style=flat-square" alt="Kwork API"></a>
    <a href="https://www.python.org"><img src="https://img.shields.io/badge/Python-3.11+-yellow?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-purple?style=flat-square" alt="MIT License"></a>
  </p>
</p>

---

Подключите Claude к вашему аккаунту Kwork — и управляйте заказами, диалогами, биржей и кворками голосом или текстом. Без скриншотов, без переключения вкладок, без ручной рутины.

## Зачем это нужно

Фрилансер на Kwork тратит 30-60 минут в день на рутину: проверить входящие, ответить клиентам, просмотреть биржу, обновить кворки. **kwork-mcp** превращает Claude в вашего ассистента, который делает это за секунды:

> **Вы:** Есть новые сообщения?
> **Claude:** 2 непрочитанных: landingpages510 спрашивает про сроки парсера, somaris благодарит за заказ.

> **Вы:** Что на бирже по Python?
> **Claude:** 9 проектов. Интересный: "CRM-система на Django" за 25 000₽, 0 откликов, осталось 18 часов.

> **Вы:** Покажи детали заказа 60185941
> **Claude:** Парсер ЦИАН, 4 000₽, заказчик landingpages510. В чате 50 сообщений, последнее от заказчика...

## 14 инструментов

### Диалоги и сообщения
| Инструмент | Что делает |
|-----------|-----------|
| `kwork_inbox` | Список диалогов с превью, временем, счётчиком непрочитанных |
| `kwork_dialog` | Полная переписка с пользователем — с контекстом заказов и статусов |
| `kwork_send` | Отправить сообщение в личку |

### Заказы
| Инструмент | Что делает |
|-----------|-----------|
| `kwork_orders` | Все заказы: активные, завершённые, отменённые |
| `kwork_order` | Детали заказа + чат — 3 параллельных API-вызова за одну команду |
| `kwork_order_message` | Написать в чат заказа (автоматически находит заказчика) |
| `kwork_order_deliver` | Сдать заказ на проверку |

### Биржа проектов
| Инструмент | Что делает |
|-----------|-----------|
| `kwork_exchange` | Просмотр биржи с фильтрами, поиском и пагинацией |
| `kwork_project` | Детали проекта: описание, бюджет, заказчик, количество откликов |
| `kwork_propose` | Подать предложение на проект |

### Кворки и аккаунт
| Инструмент | Что делает |
|-----------|-----------|
| `kwork_my_kworks` | Список ваших кворков со статусами и ценами |
| `kwork_kwork_toggle` | Включить / поставить на паузу кворк |
| `kwork_stats` | Баланс, рейтинг, количество заказов, коннекты |
| `kwork_screenshot` | Скриншот любой страницы Kwork |

## Быстрый старт

### 1. Установка

```bash
git clone https://github.com/OpenClaw/kwork-mcp.git
cd kwork-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Настройка

```bash
cp .env.example .env
```

Заполните `.env` своими данными:

```env
KWORK_LOGIN=your_email@example.com
KWORK_PASSWORD=your_password
```

### 3. Подключение

<details>
<summary><strong>Claude Desktop</strong></summary>

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

</details>

<details>
<summary><strong>Claude Code</strong></summary>

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

</details>

<details>
<summary><strong>Другие MCP-клиенты</strong></summary>

Сервер работает через stdio — совместим с любым MCP-клиентом:

```bash
python3 server.py
```

</details>

### 4. Playwright (опционально)

Для инструментов `kwork_propose` и `kwork_screenshot` нужны cookies из браузера. Экспортируйте их через [Cookie-Editor](https://cookie-editor.cgagnier.ca/) в файл `cookies.json`:

```json
[
  {"name": "PHPSESSID", "value": "...", "domain": "kwork.ru", "path": "/"},
  {"name": "csrf_user_token", "value": "...", "domain": "kwork.ru", "path": "/"}
]
```

> 12 из 14 инструментов работают полностью через API — без cookies и браузера.

## Как это работает

```
┌─────────────┐     stdio      ┌──────────────┐     HTTP      ┌──────────────┐
│  Claude /    │◄──────────────►│   server.py  │◄────────────►│ api.kwork.ru │
│  MCP Client │                │  (FastMCP)   │              │              │
└─────────────┘                │              │   Playwright  │  kwork.ru    │
                               │              │◄─ ─ ─ ─ ─ ─ ►│  (fallback)  │
                               └──────────────┘              └──────────────┘
```

- **`kwork_api.py`** — async HTTP-клиент для `api.kwork.ru`. Автоматическая авторизация, кеширование токена, auto-refresh
- **`kwork_browser.py`** — Playwright fallback. Запускается лениво, только когда нужен
- **`server.py`** — 14 MCP-инструментов на FastMCP

**Почему API, а не парсинг?** Быстрее в 10x, надёжнее, не ломается при обновлении вёрстки. Kwork имеет полноценный REST API для мобильного приложения — мы его используем.

## Безопасность

- Логин и пароль хранятся локально в `.env` (в `.gitignore`)
- Токен API кешируется в `.kwork_token.json` с правами `600`
- Cookies браузера — в `cookies.json` (в `.gitignore`)
- Никакие данные не отправляются третьим сторонам — только прямое соединение с `api.kwork.ru`

## Лицензия

[MIT](LICENSE)

---

<p align="center">
  <sub>Сделано для фрилансеров, которые ценят своё время</sub>
</p>
