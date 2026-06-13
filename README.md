# Aloha Unofficial API

Unofficial Python integrations for Aloha.

## Integrations

- `aloha_get_check_detail.py` - `get_check_detail` (72,370 live events).
- `aloha_search_checks.py` - `search_checks` (5,368 live events).
- `aloha_get_check_detail_alt.py` - `get_check_detail_alt` (2,141 live events).
- `aloha_list_stores.py` - `list_stores` (24 live events).

## Usage

Each file exposes a `run(input, context)` entrypoint. The runtime is expected to provide:

- `input`: integration-specific request fields.
- `context["headers"]`: authenticated request headers when required.
- `context["base_url"]`: the platform base URL when overriding the default.

Install dependencies:

```bash
pip install -r requirements.txt
```

## Info

This unofficial API is built by [Integuru.ai](https://integuru.ai/).

For custom requests or hosted authentication, contact richard@taiki.online.

See the [complete list of APIs by Integuru](https://github.com/Integuru-AI/APIs-by-Integuru).
