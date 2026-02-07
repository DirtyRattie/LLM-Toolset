# ⚡ LLM API Tester

A single-file visual tool for testing LLM API endpoints. Zero dependencies, runs in browser.

![HTML](https://img.shields.io/badge/HTML-single%20file-blue) ![Python](https://img.shields.io/badge/Python-3.7+-green) ![License](https://img.shields.io/badge/license-MIT-grey)

## Quick Start

```bash
python server.py
# Open http://localhost:7860
```

That's it. The Python server handles both page hosting and CORS proxying.

> **Port conflict?** The server auto-detects if the default port is occupied and switches to the next available one. Use `-p` to specify a preferred port:
> ```bash
> python server.py -p 8080
> ```

> **Why not just open the HTML directly?** Browsers block cross-origin API requests from `file://`. The local server solves this by proxying requests through localhost.

## Features

**Providers** — OpenAI, Anthropic, Google Gemini, Custom (OpenAI-compatible) presets with one-click switching

**Model Discovery** — Fetch available models from any endpoint's `/v1/models` API

**Parameters** — All common params with slider + manual input:

| Parameter | Range | Notes |
|-----------|-------|-------|
| Temperature | 0 – 2 | Slider + number input |
| Max Tokens | 1 – 128,000 | Slider caps at 16K, input goes higher |
| Top P | 0 – 1 | Advanced panel |
| Frequency Penalty | -2 – 2 | OpenAI / Custom only |
| Presence Penalty | -2 – 2 | OpenAI / Custom only |
| Seed | integer | OpenAI / Custom only |
| Stop Sequences | comma-separated | All providers |
| Stream | on/off | Not available for Gemini |

**Response Panel** — 6 tabs:

- **Response** — Parsed assistant reply
- **Raw JSON** — Full response with syntax highlighting
- **Request** — Outgoing payload inspector
- **Headers** — Response headers
- **⚠ Error** — Structured error log with diagnostics (appears on failure)
- **History** — Last 20 requests, click to review

**Error Diagnostics** — Auto-detects error type/code/param, provides hints for common issues (CORS, timeout, SSL), one-click copy full error log

## Files

```
├── llm-api-tester.html   # Frontend (self-contained, no build step)
├── server.py              # CORS proxy server (Python 3, zero dependencies)
└── README.md
```

## Usage

### With proxy (recommended)

```bash
python server.py              # default port 7860, auto-finds if occupied
python server.py -p 8080      # preferred port
```

### Without proxy

Open `llm-api-tester.html` directly in a browser. Works for APIs that return proper CORS headers (rare). Most will fail — use the proxy.

### Custom / OpenAI-compatible endpoints

Select **Custom (OpenAI-compat)**, enter your base URL and key. Works with:

- Ollama (`http://localhost:11434/v1/chat/completions`)
- LM Studio, vLLM, LocalAI
- Together, Groq, DeepSeek, Moonshot, etc.
- Any OpenAI-compatible API

## License

MIT
