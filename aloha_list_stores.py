from curl_cffi import requests
from html.parser import HTMLParser
import json
from urllib.parse import urlparse


DEFAULT_BASE_URL = "https://<SUBDOMAIN>.alohaenterprise.com"
DEFAULT_COMPANY = "hoo14"


def run(headers, user_input):
    """Fetch available Aloha stores and enrich each store with address details."""
    cookie = headers.get("Cookie") if isinstance(headers, dict) else ""
    if not cookie:
        return _auth_error("Missing Aloha session cookie")

    app_id = str(user_input.get("app_id", 2))
    company = str(user_input.get("company", DEFAULT_COMPANY))
    include_raw = bool(user_input.get("include_raw", True))
    base_url = _origin(globals().get("BASE_URL") or DEFAULT_BASE_URL)
    url = f"{base_url}/servlet/exportbuilder"

    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
    }

    try:
        response = requests.get(
            url,
            params={"appId": app_id, "requestType": "getStoreList"},
            headers=request_headers,
            impersonate="chrome131",
            timeout=30,
        )
    except Exception as exc:
        return {
            "status_code": 502,
            "success": False,
            "body": {"error": f"Failed to request Aloha store list: {exc}"},
        }

    text = response.text or ""
    lowered = text.lower()
    if response.status_code in (401, 403) or "unauthorized" in lowered or "login.do" in lowered:
        return _auth_error("Aloha session expired or unauthorized")
    if response.status_code >= 400:
        return {
            "status_code": response.status_code,
            "success": False,
            "body": {"error": "Aloha store list request failed", "response": text[:1000]},
        }

    try:
        raw_stores = _loads_xssi_json(text)
    except Exception as exc:
        return {
            "status_code": 502,
            "success": False,
            "body": {"error": f"Aloha returned invalid store JSON: {exc}", "response": text[:1000]},
        }

    if not isinstance(raw_stores, list):
        return {
            "status_code": 502,
            "success": False,
            "body": {"error": "Aloha store response was not a list", "response": raw_stores},
        }

    stores = [_normalize_store(store) for store in raw_stores if isinstance(store, dict)]
    token_result = _get_store_setup_token(base_url, request_headers)
    errors = []

    if token_result.get("error"):
        errors.append({"stage": "store_setup_token", "error": token_result["error"]})
        for store in stores:
            store["address_source"] = "unavailable"
    else:
        token = token_result["token"]
        for store in stores:
            detail = _fetch_store_detail(base_url, request_headers, company, token, store)
            if detail.get("error"):
                errors.append({
                    "stage": "store_detail",
                    "store_id": store.get("id"),
                    "error": detail["error"],
                })
                store["address_source"] = "unavailable"
                continue
            store.update(detail["fields"])
            store["address_source"] = "sitesetup"

    body = {
        "count": len(stores),
        "stores": stores,
        "partial_success": len(errors) == 0,
        "errors": errors,
    }
    if include_raw:
        body["raw_stores"] = raw_stores

    return {
        "status_code": 200,
        "success": True,
        "body": body,
    }


def _normalize_store(store):
    store_id = store.get("ID", store.get("id", store.get("storeId")))
    name = store.get("Name", store.get("name", ""))
    display_name = str(name)
    suffix = f" - {store_id}"
    clean_name = display_name[:-len(suffix)] if display_name.endswith(suffix) else display_name
    return {
        "id": store_id,
        "name": clean_name,
        "display_name": display_name,
    }


def _get_store_setup_token(base_url, headers):
    try:
        response = requests.get(
            f"{base_url}/sitesetup/ss_store_list.jsp",
            headers={
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            impersonate="chrome131",
            timeout=30,
            allow_redirects=False,
        )
    except Exception as exc:
        return {"error": f"Failed to request store setup list: {exc}"}

    html = response.text or ""
    lowered = html.lower()
    if response.status_code in (301, 302, 401, 403) or "login.do" in lowered or "login.jsp" in lowered:
        return {"error": "Aloha store setup page redirected or unauthorized"}
    if response.status_code >= 400:
        return {"error": f"Aloha store setup list returned HTTP {response.status_code}"}

    token = _extract_input_value(html, "token")
    if not token:
        return {"error": "Aloha store setup list did not include a token"}
    return {"token": token}


def _fetch_store_detail(base_url, headers, company, token, store):
    store_id = store.get("id")
    store_name = store.get("name") or store.get("display_name") or ""
    payload = {
        "txtID": str(store_id),
        "txtName": store_name,
        "CID": "",
        "newrec": "0",
        "M": "",
        "userDB": company,
        "resellerOverride": "0",
        "storeCount": "0",
        "token": token,
    }
    try:
        response = requests.post(
            f"{base_url}/sitesetup/ss_store_modify.jsp",
            data=payload,
            headers={
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{base_url}/sitesetup/ss_store_list.jsp",
            },
            impersonate="chrome131",
            timeout=30,
            allow_redirects=False,
        )
    except Exception as exc:
        return {"error": f"Failed to request store detail: {exc}"}

    html = response.text or ""
    lowered = html.lower()
    if response.status_code in (301, 302, 401, 403) or "login.do" in lowered or "login.jsp" in lowered:
        return {"error": "Aloha store detail page redirected or unauthorized"}
    if response.status_code >= 400:
        return {"error": f"Aloha store detail returned HTTP {response.status_code}"}

    fields = {
        "address": _extract_input_value(html, "txtAddress"),
        "city": _extract_input_value(html, "txtCity"),
        "state": _extract_input_value(html, "txtState"),
        "zip_code": _extract_input_value(html, "txtZipCode"),
        "country": _extract_input_value(html, "txtCountry"),
        "phone": _extract_input_value(html, "txtStoreVoiceNumber"),
        "open_date": _extract_input_value(html, "txtOpenDate"),
        "poll_start_date": _extract_input_value(html, "txtPollStart"),
        "poll_end_date": _extract_input_value(html, "txtPollEnd"),
        "open_24_hours": _extract_checkbox_checked(html, "chkOpen24Hours"),
    }
    open_hour = _extract_select_default(html, "selOpenHour")
    open_minute = _extract_select_default(html, "selOpenMinute")
    close_hour = _extract_select_default(html, "selCloseHour")
    close_minute = _extract_select_default(html, "selCloseMinute")
    fields.update({
        "open_hour": open_hour,
        "open_minute": open_minute,
        "close_hour": close_hour,
        "close_minute": close_minute,
        "open_time": _format_time(open_hour, open_minute),
        "close_time": _format_time(close_hour, close_minute),
    })
    detail_name = _extract_input_value(html, "txtStoreName")
    if detail_name:
        fields["name"] = detail_name
    fields["full_address"] = _full_address(fields)
    return {"fields": fields}


class _InputParser(HTMLParser):
    def __init__(self, wanted_name):
        super().__init__()
        self.wanted_name = wanted_name.lower()
        self.value = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input":
            return
        data = {key.lower(): value for key, value in attrs}
        if data.get("name", "").lower() == self.wanted_name:
            self.value = data.get("value", "")


class _SelectParser(HTMLParser):
    def __init__(self, wanted_name):
        super().__init__()
        self.wanted_name = wanted_name.lower()
        self.value = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "select":
            return
        data = {key.lower(): value for key, value in attrs}
        if data.get("name", "").lower() == self.wanted_name:
            self.value = data.get("d")


class _CheckboxParser(HTMLParser):
    def __init__(self, wanted_name):
        super().__init__()
        self.wanted_name = wanted_name.lower()
        self.checked = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input":
            return
        data = {key.lower(): value for key, value in attrs}
        if data.get("name", "").lower() == self.wanted_name:
            self.checked = "checked" in data


def _extract_input_value(html, name):
    parser = _InputParser(name)
    parser.feed(html or "")
    return parser.value


def _extract_select_default(html, name):
    parser = _SelectParser(name)
    parser.feed(html or "")
    value = parser.value
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _extract_checkbox_checked(html, name):
    parser = _CheckboxParser(name)
    parser.feed(html or "")
    return parser.checked


def _format_time(hour, minute):
    if hour is None or minute is None:
        return None
    try:
        return f"{int(hour):02d}:{int(minute):02d}"
    except (TypeError, ValueError):
        return None


def _full_address(fields):
    line1 = fields.get("address")
    city = fields.get("city")
    state = fields.get("state")
    zip_code = fields.get("zip_code")
    country = fields.get("country")
    city_state = " ".join(part for part in [state, zip_code] if part)
    line2 = ", ".join(part for part in [city, city_state] if part)
    return ", ".join(part for part in [line1, line2, country] if part) or None


def _loads_xssi_json(text):
    cleaned = text.strip()
    if cleaned.startswith(")]}',"):
        cleaned = cleaned.split("\n", 1)[1].strip()
    return json.loads(cleaned)


def _origin(value):
    parsed = urlparse(str(value or DEFAULT_BASE_URL).strip())
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return DEFAULT_BASE_URL


def _auth_error(message):
    return {
        "status_code": 401,
        "success": False,
        "body": {"error": message},
    }
