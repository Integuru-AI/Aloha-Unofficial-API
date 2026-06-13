from curl_cffi import requests
from urllib.parse import urlparse
from urllib.parse import urlencode


DEFAULT_BASE_URL = "https://<SUBDOMAIN>.alohaenterprise.com"


def run(headers, user_input):
    """Search Aloha checks for a business date."""
    params = {
        "checkNumber": str(user_input.get("check_number", "")),
        "company": str(user_input.get("company", "hoo14")),
        "dateOfBusiness": _format_business_date(user_input.get("date_of_business", "5/6/2026")),
        "filter": str(user_input.get("filter", 0)),
        "store": str(user_input.get("store", 1)),
        "timeFrom": str(user_input.get("time_from", -1)),
        "timeTo": str(user_input.get("time_to", -1)),
    }

    base_url = _origin(globals().get("BASE_URL") or DEFAULT_BASE_URL)
    url = f"{base_url}/servlet/checkSearch"

    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.8",
        "Referer": f"{base_url}/checkviewer/checkviewer.jsp",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
    }

    cookie = headers.get("Cookie") if isinstance(headers, dict) else None
    if cookie:
        request_headers["Cookie"] = cookie

    try:
        response = requests.get(
            url,
            params=params,
            headers=request_headers,
            impersonate="chrome131",
            timeout=30,
        )
    except Exception as exc:
        return {
            "status_code": 502,
            "success": False,
            "body": {"error": f"Failed to request Aloha check search: {exc}"},
        }

    body = _parse_response_body(response)
    if response.status_code in (401, 403) or _looks_like_login_or_auth_error(body):
        return {
            "status_code": 401,
            "success": False,
            "body": {
                "error": "Aloha session expired or unauthorized",
                "query": params,
            },
        }

    if response.status_code >= 400:
        return {
            "status_code": response.status_code,
            "success": False,
            "body": {
                "error": "Aloha check search failed",
                "query": params,
                "response": body,
            },
        }

    if not isinstance(body, dict):
        return {
            "status_code": 502,
            "success": False,
            "body": {
                "error": "Aloha returned a non-JSON response",
                "query": params,
                "response": body,
            },
        }

    checks = body.get("checks")
    count = body.get("count")
    if not isinstance(checks, list):
        return {
            "status_code": 502,
            "success": False,
            "body": {
                "error": "Aloha response did not include a checks list",
                "query": params,
                "response": body,
            },
        }

    checks = [
        {
            **check,
            "date_of_business": params["dateOfBusiness"],
        }
        if isinstance(check, dict)
        else check
        for check in checks
    ]

    return {
        "status_code": 200,
        "success": True,
        "body": {
            "count": count if isinstance(count, int) else len(checks),
            "checks": checks,
            "query": params,
            "url": f"{url}?{urlencode(params)}",
        },
    }


def _format_business_date(value):
    text = str(value or "").strip()
    if not text:
        return "5/6/2026"

    parts = text.split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = parts
        return f"{int(month)}/{int(day)}/{year}"

    return text


def _origin(value):
    parsed = urlparse(str(value or DEFAULT_BASE_URL).strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return DEFAULT_BASE_URL


def _parse_response_body(response):
    try:
        return response.json()
    except Exception:
        text = (response.text or "").strip()
        return text[:2000]


def _looks_like_login_or_auth_error(body):
    if not isinstance(body, str):
        return False
    lowered = body.lower()
    return (
        "unauthorized" in lowered
        or "problem accessing /servlet/checksearch" in lowered
        or "login" in lowered
        or "sign in" in lowered
    )
