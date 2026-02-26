# kwork-mcp

MCP server for [Kwork.ru](https://kwork.ru) — the Russian freelance marketplace. Provides tools for managing inbox, orders, project exchange, and account stats through the Model Context Protocol.

Built with [FastMCP](https://github.com/jlowin/fastmcp) + [Playwright](https://playwright.dev/) for browser automation.

## Tools

| Tool | Description |
|------|-------------|
| `kwork_inbox_list` | List inbox conversations with previews, times, unread counts |
| `kwork_inbox_read` | Read full conversation with a user by ID or username |
| `kwork_inbox_send` | Send a message to a user in inbox |
| `kwork_order_list` | List active orders with IDs and titles |
| `kwork_order_read` | Read order details and chat messages |
| `kwork_order_send` | Send a message in order chat |
| `kwork_exchange_browse` | Browse project exchange with optional category/page |
| `kwork_stats` | Get connects remaining and account balance |

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

### Authentication

Export your Kwork session cookies to `cookies.json` in the project root. The file should contain an array of cookie objects:

```json
[
  {
    "name": "PHPSESSID",
    "value": "...",
    "domain": "kwork.ru",
    "path": "/"
  }
]
```

You can export cookies using browser extensions like [EditThisCookie](https://www.editthiscookie.com/) or [Cookie-Editor](https://cookie-editor.cgagnier.ca/).

## Usage

### With Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kwork": {
      "command": "python3",
      "args": ["/path/to/kwork-mcp/server.py"]
    }
  }
}
```

### Standalone

```bash
python3 server.py
```

The server communicates over stdio using the MCP protocol.

## How it works

The server uses Playwright to automate a headless Chromium browser with your Kwork session cookies. Each tool navigates to the relevant Kwork page and extracts data from the DOM using JavaScript evaluation.

- **Inbox**: Navigates to `/inbox`, clicks on conversations, reads messages from `.js-message-block` elements
- **Orders**: Uses `/manage_orders` for listing, `/track?id=` for reading/sending
- **Exchange**: Parses project cards from `/projects`
- **Stats**: Reads connects from `/projects` and balance from `/balance`

## License

MIT
