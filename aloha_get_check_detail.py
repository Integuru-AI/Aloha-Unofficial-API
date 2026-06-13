import re
import json
from curl_cffi import requests


def run(headers, user_input):
    """Fetch check detail including receipt items, totals, payments, and void information."""
    base_url = BASE_URL

    # Validate input
    unique_id = user_input.get("unique_id")
    if not unique_id:
        return {"status_code": 400, "body": {"error": "unique_id is required"}}

    show_id = user_input.get("show_id", "false")

    req_headers = {
        "Cookie": headers.get("Cookie", ""),
        "token": headers.get("token", ""),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{base_url}/checkviewer/checkviewer.jsp",
    }

    # Step 1: Fetch check receipt HTML
    receipt_resp = requests.get(
        f"{base_url}/checkviewer/checkdetail.jsp",
        params={"uniqueID": str(unique_id), "showID": show_id},
        headers=req_headers,
        impersonate="chrome110",
        timeout=30,
    )

    if receipt_resp.status_code != 200:
        if "login" in receipt_resp.url.lower():
            return {"status_code": 401, "body": {"error": "Session expired"}}
        return {"status_code": receipt_resp.status_code, "body": {"error": "Failed to fetch check detail"}}

    if "login.do" in receipt_resp.url or "login.jsp" in receipt_resp.url:
        return {"status_code": 401, "body": {"error": "Session expired"}}

    html = receipt_resp.text

    # Step 2: Parse the receipt
    result = _parse_receipt(html)
    result["unique_id"] = str(unique_id)
    result["url"] = f"{base_url}/checkviewer/checkdetail.jsp?uniqueID={unique_id}"

    # Step 3: Fetch void and comp info via checkSearch
    store_id = result.get("store_id")
    date_of_business = result.get("date_of_business")

    voids = []
    void_check_number = ""
    comps = []
    comps_amount = 0
    promos_amount = 0

    if store_id and date_of_business:
        search_headers = {
            "Cookie": headers.get("Cookie", ""),
            "token": headers.get("token", ""),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{base_url}/checkviewer/checkviewer.jsp",
        }

        try:
            search_resp = requests.get(
                f"{base_url}/servlet/checkSearch",
                params={
                    "company": _get_company(base_url),
                    "store": str(store_id),
                    "dateOfBusiness": date_of_business,
                    "filter": "0",
                    "checkNumber": "",
                    "timeFrom": "-1",
                    "timeTo": "-1",
                },
                headers=search_headers,
                impersonate="chrome110",
                timeout=30,
            )

            if search_resp.status_code == 200:
                search_text = search_resp.text
                if search_text.startswith(")]}'"):
                    search_text = search_text[search_text.index("\n") + 1:]
                search_data = json.loads(search_text)
                checks = search_data.get("checks", [])

                # Find our check by uniqueID
                for check in checks:
                    if str(check.get("uniqueID")) == str(unique_id):
                        void_check_number = check.get("voidCheckNumber", "")
                        comps_amount = check.get("comps", 0)
                        promos_amount = check.get("promos", 0)
                        break

                # If voided, fetch void details from DDV
                if void_check_number:
                    voids = _fetch_void_details(
                        base_url, headers, store_id, date_of_business,
                        result.get("check_number", ""), result.get("store_name", "")
                    )

                # If comped, fetch comp details from DDV
                if comps_amount:
                    comps = _fetch_comp_details(
                        base_url, headers, store_id, date_of_business,
                        result.get("check_number", ""), result.get("store_name", "")
                    )
        except Exception:
            pass  # Continue without void/comp info

    result["is_voided"] = bool(void_check_number)
    result["void_check_number"] = void_check_number
    result["voids"] = voids
    result["comps_amount"] = comps_amount
    result["promos_amount"] = promos_amount
    result["comps"] = comps

    return {"status_code": 200, "body": result}


# === PRIVATE ===


def _get_company(base_url):
    """Extract company code from the base URL subdomain."""
    import urllib.parse
    parsed = urllib.parse.urlparse(base_url)
    subdomain = parsed.hostname.split(".")[0] if parsed.hostname else ""
    return "hoo14"


def _fetch_void_details(base_url, headers, store_id, date_of_business, check_number, store_name):
    """Fetch detailed void info from DDV voids endpoints."""
    voids = []

    req_headers = {
        "Cookie": headers.get("Cookie", ""),
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": base_url,
        "Referer": f"{base_url}/ddv/ddv_parse.jsp",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "token": headers.get("token", ""),
    }

    common_params = {
        "StoreID": str(store_id),
        "RegionID": "0",
        "AreaID": "0",
        "BaseName": store_name,
        "StoreName": store_name,
        "StartDate": date_of_business,
        "EndDate": date_of_business,
        "SelDate": date_of_business,
        "bCanPrint": "1",
    }

    try:
        voids_resp = requests.post(
            f"{base_url}/ddv/ddv_parse_voids.jsp",
            data=common_params,
            headers=req_headers,
            impersonate="chrome110",
            timeout=30,
        )

        if voids_resp.status_code != 200:
            return voids

        void_reasons = _parse_void_reasons(voids_resp.text)

        for void_id, void_name in void_reasons:
            checklist_params = {**common_params, "VoidID": str(void_id), "VoidName": void_name}

            checklist_resp = requests.post(
                f"{base_url}/ddv/ddv_parse_voids_checklist.jsp",
                data=checklist_params,
                headers=req_headers,
                impersonate="chrome110",
                timeout=30,
            )

            if checklist_resp.status_code == 200:
                check_voids = _parse_voids_for_check(
                    checklist_resp.text, check_number, void_name
                )
                voids.extend(check_voids)
    except Exception:
        pass

    return voids


def _fetch_comp_details(base_url, headers, store_id, date_of_business, check_number, store_name):
    """Fetch detailed comp info from DDV comps endpoints."""
    comps = []

    req_headers = {
        "Cookie": headers.get("Cookie", ""),
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": base_url,
        "Referer": f"{base_url}/ddv/ddv_parse.jsp",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "token": headers.get("token", ""),
    }

    common_params = {
        "StoreID": str(store_id),
        "RegionID": "0",
        "AreaID": "0",
        "BaseName": store_name,
        "StoreName": store_name,
        "StartDate": date_of_business,
        "EndDate": date_of_business,
        "SelDate": date_of_business,
        "bCanPrint": "1",
    }

    try:
        comps_resp = requests.post(
            f"{base_url}/ddv/ddv_parse_comps.jsp",
            data=common_params,
            headers=req_headers,
            impersonate="chrome110",
            timeout=30,
        )

        if comps_resp.status_code != 200:
            return comps

        comp_reasons = _parse_comp_reasons(comps_resp.text)

        for comp_id, comp_name in comp_reasons:
            checklist_params = {**common_params, "CompID": str(comp_id), "CompName": comp_name}

            checklist_resp = requests.post(
                f"{base_url}/ddv/ddv_parse_comps_checklist.jsp",
                data=checklist_params,
                headers=req_headers,
                impersonate="chrome110",
                timeout=30,
            )

            if checklist_resp.status_code != 200:
                continue

            if not _check_in_comp_checklist(checklist_resp.text, check_number):
                continue

            check_params = {**checklist_params, "CheckID": check_number}
            check_resp = requests.post(
                f"{base_url}/ddv/ddv_parse_comps_check.jsp",
                data=check_params,
                headers=req_headers,
                impersonate="chrome110",
                timeout=30,
            )

            if check_resp.status_code == 200:
                check_comps = _parse_comp_items(check_resp.text, comp_name)
                comps.extend(check_comps)
    except Exception:
        pass

    return comps


def _parse_comp_reasons(html):
    """Parse comps summary HTML to extract comp reason IDs and names."""
    reasons = []
    pattern = re.compile(r"c\((\d+),'([^']+)'\)")
    for match in pattern.finditer(html):
        reasons.append((match.group(1), match.group(2)))
    return reasons


def _check_in_comp_checklist(html, check_number):
    """Check if a specific check number appears in the comp checklist."""
    return f"c({check_number})" in html


def _parse_comp_items(html, comp_reason):
    """Parse comp check detail HTML to extract comped items."""
    items = []
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL)

    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 5:
            continue

        item_name = _strip_html(cells[0]).strip()
        if not item_name or item_name == "Item Name":
            continue

        order_time = _strip_html(cells[2]).strip() if len(cells) > 2 else ""
        amount_str = _strip_html(cells[4]).strip().replace(",", "") if len(cells) > 4 else ""

        try:
            amount = float(amount_str)
        except (ValueError, TypeError):
            continue

        items.append({
            "comp_reason": comp_reason,
            "menu_item": item_name,
            "order_time": order_time,
            "amount": amount,
        })

    return items


def _parse_receipt(html):
    """Parse the checkdetail.jsp receipt HTML."""
    result = {
        "store_id": None,
        "store_name": "",
        "address_lines": [],
        "employee": "",
        "employee_id": "",
        "employee_name": "",
        "date_of_business": "",
        "open_time": "",
        "close_time": "",
        "check_number": "",
        "table": "",
        "guests": "",
        "items": [],
        "totals": {},
        "payments": [],
        "raw_rows": [],
    }

    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL)

    if len(tables) >= 1:
        rows = re.findall(r"<td[^>]*>(.*?)</td>", tables[0], re.DOTALL)
        if rows:
            store_line = _strip_html(rows[0])
            store_match = re.match(r"(\d+)\s*-\s*(.*)", store_line)
            if store_match:
                result["store_id"] = int(store_match.group(1))
                result["store_name"] = store_match.group(2).strip()
            for r in rows[1:]:
                line = _strip_html(r).strip()
                if line and line != "&nbsp;" and line != "x":
                    result["address_lines"].append(line)

    if len(tables) >= 2:
        _parse_header_table(tables[1], result)

    if len(tables) >= 3:
        _parse_items_table(tables[2], result)

    return result


def _parse_header_table(table_html, result):
    """Parse the header table with employee, times, etc."""
    rows = re.findall(r"<tr>(.*?)</tr>", table_html, re.DOTALL)

    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if not cells:
            continue

        first_cell = _strip_html(cells[0]).strip()

        if "Employee:" in first_cell:
            if len(cells) >= 2:
                emp = _strip_html(cells[1]).strip()
                result["employee"] = emp
                emp_match = re.match(r"(\d+)\s*-\s*(.*)", emp)
                if emp_match:
                    result["employee_id"] = emp_match.group(1)
                    result["employee_name"] = emp_match.group(2).strip()
            if len(cells) >= 3:
                result["date_of_business"] = _strip_html(cells[2]).strip()

        elif "Open Time:" in first_cell:
            if len(cells) >= 2:
                result["open_time"] = _strip_html(cells[1]).strip()
            if len(cells) >= 3:
                result["check_number"] = _strip_html(cells[2]).strip()

        elif "Close Time:" in first_cell:
            if len(cells) >= 2:
                result["close_time"] = _strip_html(cells[1]).strip()

        elif "Table:" in first_cell:
            if len(cells) >= 2:
                result["table"] = _strip_html(cells[1]).strip()

        elif "Guests:" in first_cell:
            if len(cells) >= 2:
                result["guests"] = _strip_html(cells[1]).strip()


def _parse_items_table(table_html, result):
    """Parse the items/totals/payments table."""
    rows = re.findall(r"<tr>(.*?)</tr>", table_html, re.DOTALL)

    section = "items"
    pending_payment = None

    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if not cells:
            continue

        first_raw = cells[0]
        first_text = _strip_html(first_raw).strip()

        if not first_text or first_text == "--End of Check History--":
            if not first_text and section == "items" and result["items"]:
                section = "totals"
            elif not first_text and section == "totals" and result["totals"]:
                section = "payments"
            continue

        row_data = [_strip_html(c).strip() for c in cells]
        result["raw_rows"].append(row_data)

        amount = None
        if len(cells) >= 2:
            amount_str = _strip_html(cells[1]).strip().replace(",", "")
            try:
                amount = float(amount_str)
            except (ValueError, TypeError):
                amount = None

        if section == "items":
            is_modifier = 'padding-left' in first_raw
            name = first_text
            if amount is not None:
                result["items"].append({
                    "name": name,
                    "amount": amount,
                    "is_modifier": is_modifier,
                })

        elif section == "totals":
            lower = first_text.lower()
            if lower == "subtotal":
                result["totals"]["subtotal"] = amount
            elif lower == "tax":
                result["totals"]["tax"] = amount
            elif lower == "total":
                if "total" not in result["totals"]:
                    result["totals"]["total"] = amount
                else:
                    result["totals"]["total"] = amount
            elif lower == "autogratuity":
                result["totals"]["autogratuity"] = amount
            elif lower == "tip":
                result["totals"]["tip"] = amount

        elif section == "payments":
            lower = first_text.lower()
            if lower == "tip":
                result["totals"]["tip"] = amount
            elif first_text.startswith("Auth:") or first_text.startswith("\xa0\xa0\xa0\xa0Auth:") or "Auth:" in first_text:
                auth_match = re.search(r"Auth:(\w+)", first_text)
                if auth_match and pending_payment:
                    pending_payment["auth"] = auth_match.group(1)
            elif amount is not None and not lower.startswith("auth"):
                tender_name = re.sub(r'\s+', ' ', first_text).strip()
                pending_payment = {
                    "tender": tender_name,
                    "amount": amount,
                    "auth": "",
                }
                result["payments"].append(pending_payment)


def _parse_void_reasons(html):
    """Parse voids summary HTML to extract void reason IDs and names."""
    reasons = []
    pattern = re.compile(r"c\((\d+),'([^']+)'\)")
    for match in pattern.finditer(html):
        reasons.append((match.group(1), match.group(2)))
    return reasons


def _parse_voids_for_check(html, check_number, void_reason):
    """Parse voids checklist HTML to find void entries for a specific check number."""
    voids = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)

    for row in rows:
        check_match = re.search(r'onClick="c\((\d+)\);"', row)
        if not check_match:
            continue

        row_check_id = check_match.group(1)
        if row_check_id != check_number:
            continue

        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 8:
            continue

        menu_item = _strip_html(cells[1]).strip()
        employee = _strip_html(cells[2]).strip()
        manager = _strip_html(cells[3]).strip()
        time_val = _strip_html(cells[4]).strip()
        count_str = _strip_html(cells[5]).strip()
        amount_str = _strip_html(cells[7]).strip().replace(",", "")

        try:
            count = int(count_str)
        except ValueError:
            count = 0

        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0.0

        voids.append({
            "void_reason": void_reason,
            "menu_item": menu_item,
            "employee": employee,
            "manager": manager,
            "time": time_val,
            "count": count,
            "amount": amount,
        })

    return voids


def _strip_html(text):
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ").strip()
