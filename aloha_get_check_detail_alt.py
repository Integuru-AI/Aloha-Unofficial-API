from curl_cffi import requests
from html.parser import HTMLParser
from urllib.parse import urlparse


DEFAULT_BASE_URL = "https://<SUBDOMAIN>.alohaenterprise.com"


def run(headers, user_input):
    """Fetch and parse an Aloha check detail receipt by unique ID."""
    cookie = headers.get("Cookie") if isinstance(headers, dict) else ""
    if not cookie:
        return _auth_error("Missing Aloha session cookie")

    unique_id = str(
        user_input.get("unique_id")
        or user_input.get("uniqueID")
        or user_input.get("id")
        or ""
    ).strip()
    if not unique_id:
        return {
            "status_code": 400,
            "success": False,
            "body": {"error": "unique_id is required"},
        }

    show_id = str(user_input.get("show_id", "false")).lower()
    base_url = _origin(globals().get("BASE_URL") or DEFAULT_BASE_URL)
    url = f"{base_url}/checkviewer/checkdetail.jsp"
    request_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.8",
        "Referer": f"{base_url}/checkviewer/checkviewer.jsp",
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
            params={"showID": show_id, "uniqueID": unique_id},
            headers=request_headers,
            impersonate="chrome131",
            timeout=30,
        )
    except Exception as exc:
        return {
            "status_code": 502,
            "success": False,
            "body": {"error": f"Failed to request Aloha check detail: {exc}"},
        }

    html = response.text or ""
    lowered = html.lower()
    if response.status_code in (401, 403) or "unauthorized" in lowered or "login.do" in lowered:
        return _auth_error("Aloha session expired or unauthorized")
    if response.status_code >= 400:
        return {
            "status_code": response.status_code,
            "success": False,
            "body": {
                "error": "Aloha check detail request failed",
                "response": html[:1000],
            },
        }

    parsed = _parse_detail(html)
    parsed["unique_id"] = unique_id
    parsed["url"] = f"{url}?showID={show_id}&uniqueID={unique_id}"
    return {
        "status_code": 200,
        "success": True,
        "body": parsed,
    }


def _parse_detail(html):
    parser = _TableParser()
    parser.feed(html or "")
    tables = [[_compact(row) for row in table] for table in parser.tables]

    store = _parse_store(tables[0] if len(tables) > 0 else [])
    header = _parse_header(tables[1] if len(tables) > 1 else [])
    detail = _parse_receipt_rows(tables[2] if len(tables) > 2 else [])

    return {
        **store,
        **header,
        **detail,
        "raw_rows": tables,
    }


def _parse_store(rows):
    clean_rows = [row for row in rows if row]
    store_line = clean_rows[0][0] if clean_rows and clean_rows[0] else ""
    store_id = None
    store_name = store_line
    if " - " in store_line:
        possible_id, possible_name = store_line.split(" - ", 1)
        store_id = possible_id.strip() or None
        store_name = possible_name.strip()

    return {
        "store_id": store_id,
        "store_name": store_name,
        "address_lines": [row[0] for row in clean_rows[1:] if row and row[0] and row[0] != "x"],
    }


def _parse_header(rows):
    result = {}
    for row in rows:
        if not row:
            continue
        label = _label(row[0])
        value = row[1].strip() if len(row) > 1 else ""
        trailing = row[2].strip() if len(row) > 2 else ""

        if label == "employee":
            result["employee"] = value
            employee_id, employee_name = _split_employee(value)
            result["employee_id"] = employee_id
            result["employee_name"] = employee_name
            result["date_of_business"] = trailing or None
        elif label == "open time":
            result["open_time"] = value or None
            result["check_number"] = trailing or None
        elif label == "close time":
            result["close_time"] = value or None
        elif label == "table":
            result["table"] = value or None
        elif label == "guests":
            result["guests"] = _number(value)

    return result


def _parse_receipt_rows(rows):
    items = []
    totals = {}
    payments = []
    current_payment = None
    section = "items"

    for row in rows:
        if not row:
            continue
        name = _clean_text(row[0])
        amount = _money(row[1]) if len(row) > 1 else None
        lower_name = name.lower()

        if lower_name == "--end of check history--":
            break
        if lower_name in ("subtotal", "tax", "total"):
            totals[_snake(lower_name)] = amount
            section = "after_total"
            continue
        if lower_name == "tip":
            totals["tip"] = amount
            section = "after_total"
            continue
        if lower_name.startswith("auth:"):
            if current_payment is not None:
                current_payment["auth"] = name.split(":", 1)[1].strip()
            else:
                payments.append({"auth": name.split(":", 1)[1].strip()})
            continue

        if section == "items":
            items.append({"name": name, "amount": amount})
        else:
            current_payment = {"tender": name, "amount": amount}
            payments.append(current_payment)

    return {
        "items": items,
        "totals": totals,
        "payments": payments,
    }


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._table = []
        self._row = []
        self._cell = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._table = []
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            self._row.append(_clean_text(" ".join(self._cell)))
            self._cell = []
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self._table.append(self._row)
            self._row = []
            self._in_row = False
        elif tag == "table" and self._in_table:
            self.tables.append(self._table)
            self._table = []
            self._in_table = False


def _compact(row):
    return [cell for cell in (_clean_text(cell) for cell in row) if cell and cell != "\xa0"]


def _clean_text(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _label(value):
    return _clean_text(value).rstrip(":").lower()


def _split_employee(value):
    text = _clean_text(value)
    if " - " not in text:
        return None, text or None
    employee_id, employee_name = text.split(" - ", 1)
    return employee_id.strip() or None, employee_name.strip() or None


def _snake(value):
    return str(value).strip().lower().replace(" ", "_")


def _number(value):
    text = _clean_text(value)
    if not text:
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return text
    return int(number) if number.is_integer() else number


def _money(value):
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text.replace("$", "").replace(",", ""))
    except ValueError:
        return text


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
