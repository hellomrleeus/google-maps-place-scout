import json
import os
import re
from typing import List, Dict, Tuple, Set, Optional, Any, Union

try:
    from opening_hours import check_place_open_status
except ImportError:
    def check_place_open_status(place, visit_date_str="", departure_time_str="", allow_dinner_only=False, max_acceptable_open_hour=17):
        return True, "营业中"

try:
    from exclusion_loader import load_exclusion_sources, load_exclusion_source, normalize_raw_record
except ImportError:
    def load_exclusion_sources(sources: Optional[Any]) -> List[Dict[str, Any]]:
        if not sources:
            return []
        if isinstance(sources, str) and os.path.exists(sources):
            try:
                with open(sources, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def load_exclusion_source(source: Optional[str]) -> List[Dict[str, Any]]:
        return load_exclusion_sources(source)

def normalize_phone(phone: str) -> str:
    if not phone or phone in ("无", "未提供", "未知", "null", "None"):
        return ""
    digits = re.sub(r"\D", "", phone)
    # Remove leading country code 1 if length is 11
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits

def normalize_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fa5]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()

# 1. Global legal, corporate, and business entity suffixes (Domain-agnostic)
LEGAL_ENTITY_SUFFIXES = {
    # English corporate, legal & organizational forms
    "inc", "incorporated", "ltd", "limited", "corp", "corporation",
    "llc", "co", "company", "holdings", "group", "enterprises",
    "international", "intl", "branch", "associates", "partners",
    "services", "solutions",
    # Chinese corporate forms
    "有限公司", "有限责任", "股份", "集团", "企业", "分公司", "办事处", "总店", "分店"
}

GENERIC_PLACE_FILLER_WORDS = {
    # Non-distinctive structural or filler terms
    "the", "and", "original", "famous", "official", "premier", "elite",
    "center", "centre", "plaza", "square", "station", "place"
}

STANDARD_ROAD_AND_UNIT_STOPWORDS = {
    # Road types & designations
    "rd", "road", "st", "street", "ave", "avenue", "dr", "drive", "blvd", "boulevard",
    "way", "lane", "ln", "court", "ct", "cres", "crescent", "place", "pl", "pkwy", "parkway",
    "terrace", "terr", "trail", "trl", "circle", "cir", "path", "walk", "row", "square", "sq",
    "highway", "hwy", "route", "rte", "expressway", "freeway", "turnpike",
    # Commercial unit & building descriptors
    "unit", "ste", "suite", "apt", "apartment", "room", "rm", "floor", "fl", "level", "lvl",
    "building", "bldg", "plaza", "centre", "center", "mall", "tower", "block", "box", "po"
}

def extract_brand_tokens(name: str, dynamic_stopwords: Optional[Set[str]] = None) -> Set[str]:
    """
    Extracts distinctive brand words ignoring domain-agnostic corporate suffixes,
    filler words, and runtime-injected dynamic category/locality stopwords.
    Does NOT contain hardcoded dining, food, or regional city keywords.
    """
    if not name:
        return set()

    clean = name.lower()
    # Normalize acronyms with dots between word characters (e.g. 'bb.q' -> 'bbq', 'u.s.a' -> 'usa')
    clean = re.sub(r"(?<=\w)\.(?=\w)", "", clean)
    clean = re.sub(r"[^\w\s\u4e00-\u9fa5]", " ", clean)
    clean = clean.replace("&", " and ")

    tokens = re.findall(r"[a-z0-9\u4e00-\u9fa5]+", clean)
    effective_stops = set(LEGAL_ENTITY_SUFFIXES) | set(GENERIC_PLACE_FILLER_WORDS)
    if dynamic_stopwords:
        effective_stops.update(s.lower().strip() for s in dynamic_stopwords if s and s.strip())

    return {t for t in tokens if t not in effective_stops and len(t) > 2}

def extract_address_features(address: str, dynamic_stopwords: Optional[Set[str]] = None) -> Tuple[Optional[str], Set[str]]:
    """Extracts street number and distinctive road tokens without hardcoding specific cities."""
    if not address:
        return None, set()
    clean = address.lower().replace(",", " ")
    num_match = re.search(r"\b(\d{1,6})\b", clean)
    street_num = num_match.group(1) if num_match else None

    effective_stops = set(STANDARD_ROAD_AND_UNIT_STOPWORDS)
    if dynamic_stopwords:
        effective_stops.update(s.lower().strip() for s in dynamic_stopwords if s and s.strip())

    tokens = re.findall(r"[a-z0-9]+", clean)
    keywords = {t for t in tokens if t not in effective_stops and len(t) > 2 and not t.isdigit()}
    return street_num, keywords

try:
    from jev_client import JevDecisionClient
except ImportError:
    JevDecisionClient = None

def detect_multi_location_brands(records: List[Dict], dynamic_stopwords: Optional[Set[str]] = None) -> Set[str]:
    """
    Dynamically identifies multi-location brands (chains) by detecting brand tokens
    that appear across multiple distinct addresses or coordinate locations in reference records.
    """
    brand_locations: Dict[str, Set[str]] = {}
    for r in records:
        name = r.get("name") or ""
        tokens = extract_brand_tokens(name, dynamic_stopwords)
        addr = r.get("address") or ""
        num, road_tokens = extract_address_features(addr, dynamic_stopwords)
        loc_key = f"{num or ''}_{'_'.join(sorted(road_tokens))}" if (num or road_tokens) else addr.strip()
        if not loc_key:
            continue
        for t in tokens:
            if len(t) < 4:
                continue
            if t not in brand_locations:
                brand_locations[t] = set()
            brand_locations[t].add(loc_key)

    return {b for b, locs in brand_locations.items() if len(locs) >= 2}

def semantic_entity_match(
    candidate: Dict,
    reference_items: List[Dict],
    jev_client: Optional[Any] = None,
    dynamic_stopwords: Optional[Set[str]] = None,
    known_chains: Optional[Set[str]] = None
) -> Tuple[bool, float, str, Optional[Dict]]:
    """
    Tier 2: Model-driven semantic entity matching for places.
    Resolves variations in branch names, brand aliases, and spatial address overlaps.
    Accurately differentiates chain branches (requiring address consistency) from
    unique independent establishments.
    Integrates TypeSafe AI Jev decision model (System One) with seamless heuristic fallback.
    Returns: (is_matched, confidence, rationale, matched_record)
    """
    c_name = candidate.get("name", "")
    c_addr = candidate.get("address", "")
    c_brand_tokens = extract_brand_tokens(c_name, dynamic_stopwords)
    c_num, c_addr_tokens = extract_address_features(c_addr, dynamic_stopwords)

    if not c_brand_tokens:
        return False, 0.0, "", None

    for ref in reference_items:
        r_name = ref.get("name", "")
        r_addr = ref.get("address", "")
        r_brand_tokens = extract_brand_tokens(r_name, dynamic_stopwords)

        common_brands = c_brand_tokens & r_brand_tokens
        if not common_brands:
            continue

        # 1. TypeSafe Jev Decision Model check (if configured and ready)
        if jev_client and getattr(jev_client, "is_ready", lambda: False)():
            jev_res = jev_client.evaluate_entity_match(candidate, ref)
            if jev_res is not None:
                is_m, conf, rationale, rel = jev_res
                if is_m:
                    return True, conf, f"Jev 决策: {rationale}", ref
                elif rel == "chain_different_branch":
                    continue

        # 2. Local heuristic rule fallback
        r_num, r_addr_tokens = extract_address_features(r_addr, dynamic_stopwords)
        shared_brand_name = list(common_brands)[0]
        is_chain = bool(known_chains and any(b in known_chains for b in common_brands))

        # Case A: Same brand and exact same street number (Definite match)
        if c_num and r_num and c_num == r_num:
            addr_overlap = c_addr_tokens & r_addr_tokens
            if addr_overlap or not c_addr_tokens or not r_addr_tokens:
                return (
                    True,
                    0.98,
                    f"核心品牌 '{shared_brand_name}' 一致，且门牌号 '{c_num}' 相同",
                    ref
                )

        # Case B: Significant address road overlap (Same Plaza / Same Street branch)
        if c_addr_tokens and r_addr_tokens:
            common_road = c_addr_tokens & r_addr_tokens
            if common_road:
                return (
                    True,
                    0.93,
                    f"核心品牌 '{shared_brand_name}' 一致，且同在 '{list(common_road)[0]}' 商圈/路段",
                    ref
                )

        # Case C: If both records have street numbers and they conflict without road overlap,
        # they are definitely at different physical locations
        if c_num and r_num and c_num != r_num and not (c_addr_tokens & r_addr_tokens):
            continue

        # Case D: Independent unique brands (when no street number conflict exists)
        if not is_chain:
            for b in common_brands:
                if len(b) >= 6:
                    return (
                        True,
                        0.89,
                        f"高特征独立品牌 '{b}' 实体高度对齐",
                        ref
                    )

    return False, 0.0, "", None

class PlaceFilter:
    def __init__(
        self,
        exclusion_sources: Optional[Union[str, List[str]]] = None,
        exclude_regions: Optional[List[str]] = None,
        include_regions: Optional[List[str]] = None,
        exclude_places: Optional[List[str]] = None,
        mandatory_places: Optional[List[str]] = None,
        visit_date: str = "",
        departure_time: str = "09:30",
        filter_closed: bool = True,
        allow_dinner_only: bool = False,
        typesafe_api_key: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
        jev_client: Optional[Any] = None,
        place_types: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        dynamic_stopwords: Optional[Any] = None,
        locality: Optional[str] = None,
        **kwargs
    ):
        self.exclusion_sources = exclusion_sources
        self.exclusion_data = load_exclusion_sources(exclusion_sources) if exclusion_sources else []
        self.exclude_regions = [r.lower().strip() for r in (exclude_regions or []) if r and r.strip()]
        self.include_regions = [r.lower().strip() for r in (include_regions or []) if r and r.strip()]
        
        # Support direct place exclusion/pinning list
        self.exclude_places = [r.lower().strip() for r in (exclude_places or []) if r and r.strip()]
        self.mandatory_places = [r.lower().strip() for r in (mandatory_places or []) if r and r.strip()]
        
        self.visit_date = visit_date
        self.departure_time = departure_time
        self.filter_closed = filter_closed
        self.allow_dinner_only = allow_dinner_only

        # Dynamic context stopwords (derived from active categories, keywords, and geography)
        self.dynamic_stopwords: Set[str] = set()
        if dynamic_stopwords:
            self.dynamic_stopwords.update(s.lower().strip() for s in dynamic_stopwords if s and str(s).strip())
        if place_types:
            for pt in place_types:
                for w in str(pt).replace("_", " ").split():
                    if len(w) > 2:
                        self.dynamic_stopwords.add(w.lower())
        if keywords:
            for kw in keywords:
                for w in str(kw).replace("_", " ").split():
                    if len(w) > 2:
                        self.dynamic_stopwords.add(w.lower())
        if self.exclude_regions:
            for reg in self.exclude_regions:
                for w in reg.split():
                    if len(w) > 2:
                        self.dynamic_stopwords.add(w.lower())
        if self.include_regions:
            for reg in self.include_regions:
                for w in reg.split():
                    if len(w) > 2:
                        self.dynamic_stopwords.add(w.lower())
        if locality and str(locality).strip():
            for w in str(locality).strip().split():
                if len(w) > 2:
                    self.dynamic_stopwords.add(w.lower())

        # Initialize Jev Decision Client
        self.jev_client = jev_client
        if self.jev_client is None and JevDecisionClient is not None:
            candidate_key = (
                typesafe_api_key
                or kwargs.get("typesafe_key")
                or kwargs.get("jev_key")
                or openrouter_api_key
                or os.environ.get("TYPESAFE_API_KEY")
                or os.environ.get("JEV_API_KEY")
                or os.environ.get("OPENROUTER_API_KEY", "")
            )
            if candidate_key:
                self.jev_client = JevDecisionClient(api_key=candidate_key)
            else:
                default_client = JevDecisionClient()
                if default_client.is_ready():
                    self.jev_client = default_client

        self._build_exclusion_indexes()
        if not self.exclusion_data:
            print("  [Info] 未配置或未指定排除数据源，跳过排除过滤 (所有候选商家平滑准入)")
        else:
            print(f"  [Info] 已加载黑名单与排除场所记录: {len(self.exclusion_data)} 条")
        if self.exclude_regions:
            print(f"  [Config] 已配置地理禁行/避开区域: {', '.join(self.exclude_regions)}")
        if self.include_regions:
            print(f"  [Config] 已配置地理限定区域: {', '.join(self.include_regions)}")
        if self.exclude_places:
            print(f"  [Config] 已配置临时排除场所: {', '.join(self.exclude_places)}")
        if self.mandatory_places:
            print(f"  [Config] 已配置指定必选场所: {', '.join(self.mandatory_places)}")
        if self.filter_closed:
            dinner_desc = "允许夜宵酒吧" if self.allow_dinner_only else "白天非营业时段自动过滤"
            print(f"  [Status] 已启用开业状态与公休日校验 (拜访日: {self.visit_date or '今日'}, 预定出发: {self.departure_time}, {dinner_desc})")
        if self.jev_client and getattr(self.jev_client, "is_ready", lambda: False)():
            print("  [AI Model] TypeSafe Jev 决策模型已就绪 (官方 SystemOne 接口)")

    def is_mandatory(self, place: Dict) -> Tuple[bool, str]:
        """Checks if a place is explicitly requested or pinned by the user."""
        if place.get("_is_pinned"):
            return True, "[用户指定必选] 已置顶为今日必拜访场所"
        if not self.mandatory_places:
            return False, ""
        name = (place.get("name") or "").lower()
        addr = (place.get("address") or "").lower()
        pid = (place.get("placeId") or place.get("id") or "").lower()
        for mand in self.mandatory_places:
            if mand and (mand in name or mand in addr or mand == pid):
                return True, f"[用户指定必选] 匹配必选关键词: '{mand}'"
        return False, ""

    def is_manually_excluded(self, place: Dict) -> Tuple[bool, str]:
        """Checks if a place is explicitly excluded by the user."""
        if not self.exclude_places:
            return False, ""
        name = (place.get("name") or "").lower()
        addr = (place.get("address") or "").lower()
        pid = (place.get("placeId") or place.get("id") or "").lower()
        for ex in self.exclude_places:
            if ex and (ex in name or ex in addr or ex == pid):
                return True, f"[手动指定排除] 命中排除场所: '{ex}'"
        return False, ""

    def is_region_excluded(self, place: Dict) -> Tuple[bool, str]:
        """Evaluates geofencing constraints."""
        addr = (place.get("address") or "").lower()
        region = (place.get("region") or "").lower()
        name = (place.get("name") or "").lower()
        full_loc = f"{addr} {region} {name}"

        for ex in self.exclude_regions:
            if ex in full_loc:
                return True, f"[地理禁行] 命中避开关键词: '{ex}'"

        if self.include_regions:
            matched = any(inc in full_loc for inc in self.include_regions)
            if not matched:
                return True, f"[区域限定] 不在指定范围内 ({', '.join(self.include_regions)})"

        return False, ""

    def _build_exclusion_indexes(self):
        self.exclusion_place_ids: Set[str] = set()
        self.exclusion_phones: Set[str] = set()
        self.exclusion_names: Set[str] = set()

        for item in self.exclusion_data:
            pid = item.get("placeId") or item.get("place_id") or item.get("id")
            if pid:
                self.exclusion_place_ids.add(pid)
            phone = normalize_phone(item.get("phone") or item.get("nationalPhoneNumber", ""))
            if phone and len(phone) >= 7:
                self.exclusion_phones.add(phone)
            name = normalize_text(item.get("name") or "")
            if name:
                self.exclusion_names.add(name)

        # Dynamically detect multi-location brands (chains) across exclusion data
        self.chain_brands: Set[str] = detect_multi_location_brands(self.exclusion_data, dynamic_stopwords=self.dynamic_stopwords)
        if self.exclude_places:
            for ep in self.exclude_places:
                tokens = extract_brand_tokens(ep, dynamic_stopwords=self.dynamic_stopwords)
                self.chain_brands.update(tokens)

    def is_excluded(self, place: Dict) -> Tuple[bool, str]:
        """
        Determines if a candidate place is in the exclusion sources / blacklist.
        Returns: (is_excluded, match_reason)
        """
        pid = place.get("placeId") or place.get("id", "")
        if pid and pid in self.exclusion_place_ids:
            return True, f"[精确匹配] Place ID: {pid}"

        phone = normalize_phone(place.get("phone") or place.get("nationalPhoneNumber", ""))
        if phone and phone in self.exclusion_phones:
            return True, f"[精确匹配] 电话号码: {phone}"

        norm_name = normalize_text(place.get("name") or "")
        if norm_name and norm_name in self.exclusion_names:
            return True, f"[精确匹配] 名称完全一致: {norm_name}"

        matched, conf, rationale, ref = semantic_entity_match(
            place,
            self.exclusion_data,
            jev_client=self.jev_client,
            dynamic_stopwords=self.dynamic_stopwords,
            known_chains=self.chain_brands
        )
        if matched and conf >= 0.70:
            ref_name = ref.get("name", "") if ref else ""
            return True, f"[模型语义对齐] 与排除名单 '{ref_name}' 匹配 ({rationale}, 置信度: {int(conf*100)}%)"

        return False, ""

    def filter_places(self, candidates: List[Dict]) -> Tuple[List[Dict], Dict]:
        accepted = []
        stats = {
            "total_input": len(candidates),
            "mandatory_count": 0,
            "excluded_manual": 0,
            "excluded_regions": 0,
            "excluded_closed": 0,
            "excluded_sources": 0,
            "hard_match_count": 0,
            "semantic_match_count": 0,
            "mandatory_details": [],
            "manual_details": [],
            "region_details": [],
            "closed_details": [],
            "exclusion_details": [],
            "accepted_count": 0
        }

        seen_pids = set()

        for r in candidates:
            pid = r.get("placeId") or r.get("id", "")
            if pid and pid in seen_pids:
                continue
            if pid:
                seen_pids.add(pid)

            # 1. Mandatory user inclusion (highest priority, overrides exclusion)
            is_mand, mand_reason = self.is_mandatory(r)
            if is_mand:
                r_copy = dict(r)
                r_copy["_is_pinned"] = True
                r_copy["_pinned_reason"] = mand_reason
                stats["mandatory_count"] += 1
                stats["mandatory_details"].append({
                    "name": r.get("name"),
                    "reason": mand_reason
                })
                accepted.append(r_copy)
                continue

            # 2. Manual place exclusion
            is_man_ex, man_reason = self.is_manually_excluded(r)
            if is_man_ex:
                stats["excluded_manual"] += 1
                stats["manual_details"].append({
                    "name": r.get("name"),
                    "reason": man_reason
                })
                continue

            # 3. Spatial geofencing constraints
            is_reg, reg_reason = self.is_region_excluded(r)
            if is_reg:
                stats["excluded_regions"] += 1
                stats["region_details"].append({
                    "name": r.get("name"),
                    "reason": reg_reason
                })
                continue

            # 4. Opening Hours & Operational Status Check
            if self.filter_closed:
                is_open, closed_reason = check_place_open_status(
                    place=r,
                    visit_date_str=self.visit_date,
                    departure_time_str=self.departure_time,
                    allow_dinner_only=self.allow_dinner_only
                )
                if not is_open:
                    stats["excluded_closed"] += 1
                    stats["closed_details"].append({
                        "name": r.get("name"),
                        "reason": closed_reason
                    })
                    continue

            # 5. Exclusion Sources & Blacklist Check
            is_ex, ex_reason = self.is_excluded(r)
            if is_ex:
                stats["excluded_sources"] += 1
                ex_tier = "semantic_model" if "[模型语义对齐]" in ex_reason else "hard"
                if ex_tier == "hard":
                    stats["hard_match_count"] += 1
                else:
                    stats["semantic_match_count"] += 1

                stats["exclusion_details"].append({
                    "name": r.get("name"),
                    "reason": ex_reason,
                    "tier": ex_tier
                })
                continue

            accepted.append(r)

        stats["accepted_count"] = len(accepted)
        return accepted, stats
