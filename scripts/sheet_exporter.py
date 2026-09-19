#!/usr/bin/env python3
"""
Sheet exporter module for Daily Field Sales Route.
Exports the finalized confirmed places to:
1. Standard CSV file with 7 columns:
   No., Place Name, Address, Navigation Address, Opening Hours, Phone, Match Evidence
2. Professionally styled Excel (.xlsx) file with openpyxl
3. Google Sheets (via Google Apps Script Webhook)
All fields are based on pure English Google Maps data.
"""

import os
import re
import csv
import traceback
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from opening_hours import format_weekday_opening_hours, normalize_time_to_english

try:
    from config_manager import extract_spreadsheet_id, get_user_cache_dir
except ImportError:
    def extract_spreadsheet_id(url_or_id: Optional[str]) -> str:
        if not url_or_id:
            return ""
        cleaned = str(url_or_id).strip()
        match = re.search(r"/spreadsheets/d/(?:e/)?([a-zA-Z0-9-_]+)", cleaned)
        return match.group(1) if match else cleaned.split("?")[0].split("#")[0].strip("/")

try:
    from directional_router import haversine_distance_km
except ImportError:
    import math
    def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0)**2
        return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

UNIVERSAL_HEADERS = [
    "No.",
    "Place Name",
    "Address",
    "Navigation Address",
    "Opening Hours",
    "Phone",
    "Match Evidence"
]

def clean_address_to_english(address: str) -> str:
    if not address:
        return ""
    addr = str(address)
    subs = [
        ("加拿大", " Canada "),
        ("安大略省", " ON "),
        ("安大略", " ON "),
        ("安省", " ON "),
        ("多伦多市", " Toronto "),
        ("多伦多", " Toronto "),
        ("万锦市", " Markham "),
        ("万锦", " Markham "),
        ("士嘉堡", " Scarborough "),
        ("北约克", " North York "),
        ("列治文山市", " Richmond Hill "),
        ("列治文山", " Richmond Hill "),
        ("密西沙加市", " Mississauga "),
        ("密西沙加", " Mississauga "),
        ("旺市", " Vaughan "),
    ]
    for ch, en in subs:
        addr = addr.replace(ch, en)
    return re.sub(r"\s+", " ", addr).strip()

STREET_SUFFIXES = r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ct|Court|Cres|Crescent|Way|Pl|Place|Pkwy|Parkway|Lane|Ln|Terr|Terrace|Trail|Trl|Walk|Circle|Cir|Gate|Path|Row|Square|Sq|Concession|Line)"
HIGHWAY_PREFIXES = r"(?:Hwy|Highway|Route|Rte)"

def canonicalize_navigation_address(address: str) -> str:
    """
    Extracts and canonicalizes the navigation base street address from detailed merchant addresses.
    Removes unit numbers, suite numbers, booth/floor/food court annotations, and mall name prefixes.
    Ensures that multiple merchants in the same commercial plaza or mall resolve to the same driving destination.
    """
    if not address:
        return ""
    addr = clean_address_to_english(address)

    # 1. Search for a street number + highway name or street name
    # E.g., extracts "5000 Hwy 7..." from "CF Markville, 5000 Hwy 7..."
    street_pattern = re.compile(
        r"(?:^|,\s*)(?P<street_addr>\d+\s+(?:"
        + HIGHWAY_PREFIXES + r"\s+[0-9A-Za-z]+|"
        + r"[A-Za-z0-9\s.'&-]+?\b" + STREET_SUFFIXES + r"\b(?:\s+[NESW]\b)?"
        + r"))(?P<rest>.*)",
        re.IGNORECASE
    )
    m = street_pattern.search(addr)
    if m:
        addr = m.group("street_addr").strip() + m.group("rest")

    # 2. Strip unit, suite, booth, kiosk, food court, floor, and mall interior descriptors
    strip_patterns = [
        r"(?i)\b(?:unit|ste|suite|apt|apartment|room|rm|space|spc|booth|kiosk)\b\s*#?\s*[\w-]+",
        r"(?i)\b(?:food\s*court|food\s*fair|foodhall|food\s*hall)\s*#?\s*[\w-]*",
        r"#\s*[\w-]+",
        r"(?i)\b(?:upper|lower|ground|concourse|basement|first|second|third|1st|2nd|3rd)\s*(?:level|floor|fl|lvl)?\b",
        r"(?i)\b(?:level|floor|fl|lvl)\s*[0-9A-Z-]+\b",
        r"(?i)\binside\s+(?:walmart|loblaws|costco|target|metro|t&t\s*supermarket|t&t|h\s*mart|superstore|the\s+mall)\b",
        r"(?i)\b(?:inside|in)\s+the\s+[\w\s]+?(?=,\s*|\s+\d|$)",
        r"(?i)\b[a-z0-9\s\x27&.-]+?\s+(?:mall|shopping\s+centre|shopping\s+center|plaza)\b",
    ]
    for p in strip_patterns:
        addr = re.sub(p, "", addr)

    # 3. Standardize road type abbreviations
    addr = re.sub(r"(?i)\bhighway\b", "Hwy", addr)
    addr = re.sub(r"(?i)\bstreet\b", "St", addr)
    addr = re.sub(r"(?i)\bavenue\b", "Ave", addr)
    addr = re.sub(r"(?i)\broad\b", "Rd", addr)
    addr = re.sub(r"(?i)\bboulevard\b", "Blvd", addr)
    addr = re.sub(r"(?i)\bdrive\b", "Dr", addr)

    # 4. Clean punctuation, duplicate commas, and excess spaces
    addr = re.sub(r"\s*,\s*", ", ", addr)
    addr = re.sub(r"(?:,\s*)+", ", ", addr)
    addr = re.sub(r"^\s*,\s*|\s*,\s*$", "", addr)
    addr = re.sub(r"\s+", " ", addr).strip()

    # For Canadian addresses without explicit country suffix, add Canada to assist Google Maps routing
    is_canadian = bool(
        re.search(r"\b[A-Za-z]\d[A-Za-z]\s*\d[A-Za-z]\d\b", addr)
        or re.search(r",\s*(ON|BC|AB|QC|MB|SK|NS|NB|NL|PE|YT|NT|NU)\b", addr, re.IGNORECASE)
        or "toronto" in addr.lower()
        or "canada" in addr.lower()
    )
    if is_canadian and "canada" not in addr.lower():
        addr = f"{addr}, Canada"

    return addr

def extract_address_tokens(addr: str) -> Tuple[str, str, str]:
    """
    Extracts (street_number, street_token, postal_code) for address clustering.
    """
    m_num = re.search(
        r"\b(\d+)\s+(Hwy\s+\d+|Highway\s+\d+|Route\s+\d+|[A-Za-z0-9\s.'&-]+?\b"
        + STREET_SUFFIXES + r")\b",
        addr,
        re.I
    )
    street_num = m_num.group(1).lower() if m_num else ""
    street_name = ""
    if m_num:
        raw_name = m_num.group(2).lower()
        raw_name = re.sub(r"\bhighway\b", "hwy", raw_name)
        raw_name = re.sub(r"\bstreet\b", "st", raw_name)
        raw_name = re.sub(r"\bavenue\b", "ave", raw_name)
        raw_name = re.sub(r"\broad\b", "rd", raw_name)
        raw_name = re.sub(r"\bboulevard\b", "blvd", raw_name)
        raw_name = re.sub(r"\bdrive\b", "dr", raw_name)
        street_name = re.sub(r"\s+", " ", raw_name).strip()

    m_post = re.search(r"\b([A-Z]\d[A-Z]\s*\d[A-Z]\d)\b", addr, re.I)
    postal = re.sub(r"\s+", "", m_post.group(1).upper()) if m_post else ""
    return street_num, street_name, postal

def cluster_navigation_addresses(stops: List[Dict]) -> List[Dict]:
    """
    Clusters and deduplicates navigation addresses across a list of place stops.
    Multiple places located inside the same mall, plaza, or commercial complex
    are assigned the exact identical canonical navigation address string.
    """
    if not stops:
        return []

    canon_list = []
    tokens = []
    for s in stops:
        existing_nav = s.get("navigation_address")
        raw_addr = existing_nav or s.get("address", "")
        canon = canonicalize_navigation_address(raw_addr)
        canon_list.append(canon)
        tokens.append(extract_address_tokens(canon or raw_addr))

    clusters = []
    visited = set()

    for i in range(len(stops)):
        if i in visited:
            continue
        cur_cluster = [i]
        visited.add(i)
        num_i, name_i, post_i = tokens[i]
        lat_i = float(stops[i].get("latitude") or 0.0)
        lng_i = float(stops[i].get("longitude") or 0.0)

        for j in range(i + 1, len(stops)):
            if j in visited:
                continue

            # Condition 1: Identical canonical navigation address
            if canon_list[i] and canon_list[j] and canon_list[i].lower() == canon_list[j].lower():
                cur_cluster.append(j)
                visited.add(j)
                continue

            num_j, name_j, post_j = tokens[j]
            lat_j = float(stops[j].get("latitude") or 0.0)
            lng_j = float(stops[j].get("longitude") or 0.0)

            dist_km = haversine_distance_km(lat_i, lng_i, lat_j, lng_j) if (lat_i and lat_j) else 999.0

            # Condition 2: Matching street number + street name, and (matching postal code or distance < 250m)
            if num_i and num_i == num_j and name_i and name_i == name_j:
                if (post_i and post_j and post_i == post_j) or dist_km < 0.25:
                    cur_cluster.append(j)
                    visited.add(j)
                    continue

        clusters.append(cur_cluster)

    out_stops = [dict(s) for s in stops]
    for cl in clusters:
        candidates = [canon_list[idx] for idx in cl if canon_list[idx]]
        if not candidates:
            rep = ""
        else:
            candidates.sort(key=lambda x: (len(re.findall(r"\b[A-Z]\d[A-Z]\b", x)), len(x)), reverse=True)
            rep = candidates[0]
        is_multi = len(cl) > 1
        for idx in cl:
            out_stops[idx]["navigation_address"] = rep
            out_stops[idx]["_is_multi_merchant_complex"] = is_multi

    return out_stops

def normalize_rationale_to_english(rationale: Optional[str], features: Optional[List[str]] = None) -> str:
    features = features or []
    if not rationale:
        if features and isinstance(features, list) and len(features) > 0:
            return f"Matched features: {', '.join(str(d) for d in features[:3])}"
        return "Verified active establishment matching criteria"

    text = str(rationale).strip()

    replacements = [
        (r"用户指定必拜访商户", "Priority visit (User pinned merchant)"),
        (r"用户指定必选商家", "Priority visit (User pinned merchant)"),
        (r"用户指定必选场所", "Priority visit (User pinned place)"),
        (r"用户指定必选", "Priority lead (User pinned)"),
        (r"Jev\s*模型决策[:：]?\s*", "Jev decision: "),
        (r"Jev\s*决策[:：]?\s*", "Jev decision: "),
        (r"品类[:：]?\s*", "category: "),
        (r"高概率(?:匹配)?", "High probability"),
        (r"中概率(?:匹配)?", "Medium probability"),
        (r"低概率(?:匹配)?", "Low probability"),
        (r"匹配", "match"),
    ]

    if features and isinstance(features, list) and len(features) > 0:
        d_sample = str(features[0])
        if d_sample not in text and d_sample != "agent-verified":
            text = f"{text} ({', '.join(str(d) for d in features[:3])})"

    for pat, repl in replacements:
        text = re.sub(pat, repl, text)

    # Collapse any duplicate spaces
    text = re.sub(r" +", " ", text)

    return text.strip()

def map_stop_to_columns(stop: Dict, idx: int) -> List:
    name = stop.get("name", "")
    address = clean_address_to_english(stop.get("address", ""))
    nav_address = stop.get("navigation_address") or canonicalize_navigation_address(address)
    
    # Run simplified opening hours algorithm
    raw_hours = stop.get("rawOpeningHours") or stop.get("openingHours")
    clean_hours = normalize_time_to_english(format_weekday_opening_hours(raw_hours))
    
    phone = stop.get("phone", "None")
    if not phone or phone in ("无", "未知", "未提供"):
        phone = "None"

    # Audit rationale / evidence
    rationale = stop.get("_audit_rationale") or stop.get("audit_rationale")
    features = stop.get("_matched_features") or []
    clean_rationale = normalize_rationale_to_english(rationale, features)

    return [
        idx + 1,
        name,
        address,
        nav_address,
        clean_hours,
        phone,
        clean_rationale
    ]

class SheetExporter:
    def __init__(
        self,
        output_dir: Optional[str] = None,
        headers: Optional[List[str]] = None,
        template: str = ""
    ):
        self.output_dir = output_dir or get_user_cache_dir("routes")
        os.makedirs(self.output_dir, exist_ok=True)
        self.headers = headers or UNIVERSAL_HEADERS

    def export_csv(self, stops: List[Dict], filename: str) -> str:
        stops = cluster_navigation_addresses(stops)
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(self.headers)
            for idx, s in enumerate(stops):
                writer.writerow(map_stop_to_columns(s, idx))
        return filepath

    def export_excel(self, stops: List[Dict], filename: str, master_nav_url: str = "") -> Optional[str]:
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        except ImportError:
            print("[Warning] openpyxl not installed. Skipping .xlsx export.")
            return None

        stops = cluster_navigation_addresses(stops)
        filepath = os.path.join(self.output_dir, filename)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Daily Visit List ({len(stops)} Stops)"

        # Title styling (A1:G1 across 7 columns)
        ws.merge_cells("A1:G1")
        title_cell = ws["A1"]
        title_cell.value = f"Daily Public Place Field Route ({len(stops)} Confirmed Stops)"
        title_cell.font = Font(name="Arial", size=14, bold=True, color="1E3A8A")
        title_cell.alignment = Alignment(horizontal="left", vertical="center")

        # Master Google Maps URL row (A2:G2 across 7 columns)
        ws.merge_cells("A2:G2")
        url_cell = ws["A2"]
        url_cell.value = "Google Maps Route Navigation"
        if master_nav_url:
            url_cell.hyperlink = master_nav_url
        url_cell.font = Font(name="Arial", size=10, bold=True, color="2563EB", underline="single")
        url_cell.alignment = Alignment(horizontal="left", vertical="center")

        # Headers styling
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid") # Dark Blue
        align_center = Alignment(horizontal="center", vertical="center")
        align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style='thin', color='D1D5DB'),
            right=Side(style='thin', color='D1D5DB'),
            top=Side(style='thin', color='D1D5DB'),
            bottom=Side(style='thin', color='D1D5DB')
        )

        # Row 4: Header (7 columns)
        ws.row_dimensions[4].height = 28
        for col_num, h_text in enumerate(self.headers, start=1):
            cell = ws.cell(row=4, column=col_num, value=h_text)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center
            cell.border = thin_border

        # Write Rows
        for idx, s in enumerate(stops):
            r_idx = idx + 5
            row_vals = map_stop_to_columns(s, idx)
            ws.row_dimensions[r_idx].height = 24
            bg_color = "F9FAFB" if idx % 2 == 0 else "FFFFFF"
            row_fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")

            for col_idx, val in enumerate(row_vals, start=1):
                cell = ws.cell(row=r_idx, column=col_idx, value=val)
                cell.fill = row_fill
                cell.border = thin_border
                if col_idx in (1, 5, 6):
                    cell.alignment = align_center
                else:
                    cell.alignment = align_left

        # Merge consecutive rows in Column 4 (Navigation Address) for multi-merchant buildings/malls
        num_stops = len(stops)
        run_start = 0
        for i in range(num_stops):
            cur_nav = stops[i].get("navigation_address") or ""
            next_nav = stops[i + 1].get("navigation_address") or "" if (i + 1 < num_stops) else None
            if cur_nav and cur_nav == next_nav:
                continue
            else:
                if i > run_start:
                    start_r = run_start + 5
                    end_r = i + 5
                    ws.merge_cells(start_row=start_r, start_column=4, end_row=end_r, end_column=4)
                    top_cell = ws.cell(row=start_r, column=4)
                    top_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
                run_start = i + 1

        # Adjust column widths for 7 columns
        col_widths = {1: 8, 2: 30, 3: 38, 4: 38, 5: 28, 6: 18, 7: 42}
        for col_idx, w in col_widths.items():
            col_letter = openpyxl.utils.get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = w

        wb.save(filepath)
        return filepath

    def export_markdown_summary(
        self,
        stops: List[Dict],
        filename: str,
        meta: Dict[str, Any]
    ) -> str:
        """
        Generates clean GFM Markdown summary document ready for direct Agent in-chat presentation.
        """
        stops = cluster_navigation_addresses(stops)
        filepath = os.path.join(self.output_dir, filename)
        visit_date = meta.get("visit_date", "Today")
        date_label = meta.get("date_label", "当日拜访")
        direction = meta.get("direction", "EAST")
        origin_str = meta.get("origin_str", "Field Operations Base")
        master_url = meta.get("master_nav_url", "")
        sheet_url = meta.get("sheet_url", "")
        legs = meta.get("legs", [])
        excel_path = meta.get("excel_path", "")
        csv_path = meta.get("csv_path", "")

        lines = [
            f"# Place Scout Route Report ({visit_date})",
            "",
            f"- **Date**: {visit_date} ({date_label})",
            f"- **Direction**: {direction}",
            f"- **Starting Base**: {origin_str}",
            f"- **Total Stops**: {len(stops)} confirmed places",
            "",
        ]
        if legs:
            lines.extend([
                "",
                "### 导航路线 (Navigation Legs)",
            ])
            for leg in legs:
                title = leg.get("title", "Leg")
                url = leg.get("url", "#")
                gps_url = leg.get("gps_url")
                if gps_url:
                    lines.append(f"- **[{title}]({url})** · [坐标导航]({gps_url})")
                else:
                    lines.append(f"- **[{title}]({url})**")

        lines.extend([
            "",
            "### 在线表格与路线总览",
        ])
        if sheet_url:
            lines.append(f"- **[Google Sheet 在线表格]({sheet_url})**")
        if master_url:
            lines.append(f"- **[全量路线总览 ({len(stops)} 站)]({master_url})**")

        lines.extend([
            "",
            "### Confirmed Stops Directory",
            "| No. | Place Name | Address | Navigation Address | Opening Hours | Phone | Match Evidence |",
            "|-----|------------|---------|--------------------|---------------|-------|----------------|",
        ])

        for idx, s in enumerate(stops):
            row_vals = map_stop_to_columns(s, idx)
            name = str(row_vals[1]).replace("|", "-")
            addr = str(row_vals[2]).replace("|", "-")
            nav = str(row_vals[3]).replace("|", "-")
            hours = str(row_vals[4]).replace("|", "-")
            phone = str(row_vals[5]).replace("|", "-")
            rationale = str(row_vals[6]).replace("|", "-")
            lines.append(f"| {idx + 1} | {name} | {addr} | {nav} | {hours} | {phone} | {rationale} |")

        if excel_path or csv_path:
            lines.extend([
                "",
                "### Exported Files"
            ])
            if excel_path:
                lines.append(f"- Excel (.xlsx): [{os.path.basename(excel_path)}](file://{excel_path})")
            if csv_path:
                lines.append(f"- CSV (.csv): [{os.path.basename(csv_path)}](file://{csv_path})")

        lines.append("")
        content = "\n".join([line for line in lines if line is not None])
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def sync_to_google_sheet(
        self,
        spreadsheet_id: str,
        credentials_file: str = "",
        sheet_name: str = "Daily Field Sales Route",
        stops: List[Dict] = None,
        master_nav_url: str = ""
    ) -> bool:
        """
        Synchronizes the finalized 30 stops exclusively via Google Apps Script Webhook.
        Zero credentials, zero Google Cloud setup, zero OAuth expiry issues.
        """
        stops = cluster_navigation_addresses(stops or [])
        webhook_url = (spreadsheet_id or "").strip()

        if not webhook_url:
            print("[Info] Google Sheet synchronization skipped (no Webhook URL configured).")
            print(f"[Info] Local Excel & Markdown exports saved successfully in: {self.output_dir}")
            return False

        # If user passed a normal Google Sheet document link instead of an Apps Script Webhook
        if "docs.google.com/spreadsheets" in webhook_url and "script.google.com" not in webhook_url:
            print(f"[Warning] Google Sheet 同步提示: 检测到您配置的是普通文档链接: {webhook_url}")
            print("   [Tip] 本项目仅采用极简、零凭据的 [Google Apps Script Webhook] 进行在线表格同步！")
            print("   [Tip] 请在您的 Google Sheet 中点击 [扩展程序 > Apps 脚本] 部署 Web 应用，并将生成的 Webhook URL (https://script.google.com/macros/s/.../exec) 配置给本技能。")
            print(f"   [Tip] 7 列规范数据已保存在本地 Excel 与 Markdown 文件中: {self.output_dir}")
            return False

        if not ("script.google.com/macros/s/" in webhook_url or webhook_url.startswith("https://script.google.com")):
            print(f"[Warning] Google Sheet Webhook 格式无效: {webhook_url}")
            print("   [Tip] 必须为 Google Apps Script Webhook URL (格式: https://script.google.com/macros/s/.../exec)")
            return False

        try:
            import json
            import urllib.request
            payload = {
                "master_nav_url": master_nav_url,
                "sheet_name": sheet_name,
                "headers": self.headers,
                "rows": [map_stop_to_columns(s, idx) for idx, s in enumerate(stops or [])]
            }
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                resp_data = resp.read().decode("utf-8")
                if resp.status in (200, 302) or "OK" in resp_data:
                    print("[Success] Google Sheet sync completed successfully via Apps Script Webhook!")
                    return True
        except Exception as e:
            print(f"[Warning] Google Sheet Webhook sync error: {e}")
            return False

    def report_failure_to_google_sheet(
        self,
        spreadsheet_id: str,
        credentials_file: Optional[str] = None,
        error: Exception = None,
        context: Optional[Dict] = None
    ) -> str:
        """
        Reports execution failure and troubleshooting advice directly into Google Sheet Webhook or local log,
        allowing users/managers to inspect root causes and retry immediately.
        """
        webhook_url = (spreadsheet_id or "").strip()
        ctx = context or {}
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        visit_date = ctx.get("visit_date", datetime.now().strftime("%Y-%m-%d"))
        direction = ctx.get("direction", "EAST")
        origin_str = ctx.get("origin_str", "Field Operations Base")

        cause, advice, retry_cmd = diagnose_error(error, ctx)
        tb_lines = traceback.format_exc().strip()

        # 1. Record incident to local log file
        log_filename = f"execution_alert_{visit_date}.log"
        log_path = os.path.join(self.output_dir, log_filename)
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"=== Google Maps Place Scout Execution Failure Alert ===\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"Visit Date: {visit_date}\n")
                f.write(f"Direction: {direction} | Origin: {origin_str}\n")
                f.write(f"Target Sheet Webhook: {webhook_url}\n")
                f.write(f"Root Cause: {cause}\n")
                f.write(f"Troubleshooting Advice: {advice}\n")
                f.write(f"Retry Command: {retry_cmd}\n")
                f.write(f"\nTraceback:\n{tb_lines}\n")
        except Exception as log_err:
            print(f"[Warning] Could not write failure log to disk: {log_err}")

        # 2. Push alert to Webhook if configured
        if webhook_url and "script.google.com" in webhook_url:
            try:
                import json
                import urllib.request
                alert_payload = {
                    "master_nav_url": "",
                    "sheet_name": f"Alert_{visit_date}",
                    "headers": ["时间", "失败原因", "排查建议", "重试命令"],
                    "rows": [[timestamp, cause, advice, retry_cmd]]
                }
                req = urllib.request.Request(
                    webhook_url,
                    data=json.dumps(alert_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=15):
                    print("[Info] Failure alert successfully published to Google Sheet via Webhook.")
            except Exception as e:
                print(f"[Warning] Could not push error alert to Google Sheet Webhook: {e}")
        else:
            if webhook_url:
                print(f"\n[Info] Google Sheet 异常状态看板: {webhook_url}")
            print(f"[Info] 详细故障排查日志已落盘至: {log_path}")

        print("\n" + "=" * 70)
        print(f"[Alert] 自主任务执行失败报警: {timestamp}")
        print("=" * 70)
        print(f"失败原因: {cause}")
        print(f"修复指引: {advice}")
        print(f"推荐重试命令: {retry_cmd}\n")
        return log_path

def diagnose_error(error: Exception, context: Optional[Dict] = None) -> Tuple[str, str, str]:
    """
    Analyzes the failure exception and execution context.
    Returns: (root_cause_summary, actionable_advice, retry_command)
    """
    err_str = str(error).lower()
    ctx = context or {}
    visit_date = ctx.get("visit_date", datetime.now().strftime("%Y-%m-%d"))
    direction = ctx.get("direction", "east")
    retry_cmd = f"python3 scripts/main.py --direction {direction} --visit-date {visit_date}"

    if "403" in err_str or "denied" in err_str or "api_key" in err_str or "api key" in err_str:
        cause = "Google Maps API Key 无效、缺失或未在 GCP 启用 Places API (New)"
        advice = "请登录 Google Cloud Console 检查 API 密钥并确保已启用 Places API (New)，或执行 `python3 scripts/main.py --setup` 重设。"
    elif "429" in err_str or "quota" in err_str or "exhausted" in err_str:
        cause = "Google Maps API 今日请求配额已超限"
        advice = "请在 Google Cloud Console 查看配额详情或提升配额限额，或等待每日配额重置后重试。"
    elif "timeout" in err_str or "timed out" in err_str or "connection" in err_str or "network" in err_str:
        cause = "网络连接超时或无法连接至外部 Google 服务"
        advice = "请检查服务器公网连接或代理设置后重试。"
    elif "少于" in err_str or "不足" in err_str or "no candidates" in err_str:
        cause = "当前走廊纵深内符合条件的场所数量不足预设目标数"
        advice = "建议增大走廊宽度（例如 `--corridor-width 6.0`）或加大探测纵深（例如 `--max-depth 35.0`）。"
        retry_cmd = f"python3 scripts/main.py --direction {direction} --corridor-width 6.0 --max-depth 35.0"
    else:
        cause = f"任务运行时发生未预期异常: {type(error).__name__} ({str(error)})"
        advice = "请查看本地日志中的详细报错堆栈定位问题，确认运行依赖及网络环境。"

    return cause, advice, retry_cmd
