#!/usr/bin/env python3
"""
Places searcher module using Google Places API (New).
Directly communicates with https://places.googleapis.com/v1/places:searchText in pure English.
"""

import os
import json
import math
import time
import gzip
import http.client
import re
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional, Tuple
from opening_hours import format_weekday_opening_hours
try:
    from cache_manager import PlaceCache
except ImportError:
    PlaceCache = None

FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.nationalPhoneNumber,"
    "places.websiteUri,"
    "places.googleMapsUri,"
    "places.rating,"
    "places.userRatingCount,"
    "places.regularOpeningHours,"
    "places.currentOpeningHours,"
    "places.priceLevel,"
    "places.primaryType,"
    "places.location,"
    "places.photos,"
    "places.editorialSummary,"
    "places.reviews,"
    "places.businessStatus,"
    "nextPageToken"
)

PRICE_MAP = {
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$"
}

# Default address-keyword -> region label mapping used when the Places API
# response carries no usable addressComponents. Override at runtime via
# set_region_aliases() (e.g. from config file "region_aliases") when scouting
# outside the default metro area.
DEFAULT_REGION_KEYWORDS = {
    "markham": "Markham",
    "scarborough": "Scarborough",
    "north york": "North York",
    "richmond hill": "Richmond Hill",
    "mississauga": "Mississauga",
    "vaughan": "Vaughan",
    "downtown": "Downtown",
}

REGION_KEYWORDS = dict(DEFAULT_REGION_KEYWORDS)


def set_region_aliases(aliases: Dict[str, str]) -> None:
    """Replaces the address-keyword -> region mapping (keys matched case-insensitively)."""
    global REGION_KEYWORDS
    REGION_KEYWORDS = {str(k).lower(): str(v) for k, v in (aliases or {}).items()}

def strip_emojis(text: str) -> str:
    if not text:
        return ""
    clean_chars = []
    for ch in text:
        code = ord(ch)
        if code > 0x1F000 or (0x2600 <= code <= 0x27BF):
            continue
        clean_chars.append(ch)
    return "".join(clean_chars).strip()

def transform_google_place(p: Dict, matched_term: str = "", default_region: str = "GTA", api_key: str = "") -> Dict:
    disp = (p.get("displayName") or {}).get("text") if isinstance(p.get("displayName"), dict) else ""
    raw_name = p.get("name") if not (str(p.get("name", "")).startswith("places/")) else ""
    name = strip_emojis(disp or raw_name or p.get("name") or "Unnamed Place")
    address = p.get("address") or p.get("formattedAddress") or ""
    address = strip_emojis(address.replace("加拿大", "Canada")).strip()
    phone = p.get("phone") or p.get("nationalPhoneNumber") or "None"
    website = p.get("website") or p.get("websiteUri") or ""
    place_id = p.get("placeId") or p.get("id") or ""
    maps_url = p.get("mapsUrl") or p.get("googleMapsUri") or f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    
    rating = float(p.get("rating", 0.0)) if p.get("rating") else 0.0
    user_count = p.get("userRatingCount")
    if user_count is not None:
        try:
            reviews_count = int(user_count)
        except Exception:
            reviews_count = 0
    elif isinstance(p.get("reviews"), list):
        reviews_count = len(p.get("reviews"))
    else:
        reviews_count = 0
    primary_type = p.get("primaryType") or "establishment"
    
    lat = float(p.get("latitude") or (p.get("location") or {}).get("latitude", 0.0))
    lng = float(p.get("longitude") or (p.get("location") or {}).get("longitude", 0.0))

    # Opening hours: run the simplified algorithm
    reg_hours = p.get("regularOpeningHours") or {}
    weekday_desc = reg_hours.get("weekdayDescriptions") if isinstance(reg_hours, dict) else None
    raw_hours = weekday_desc or p.get("openingHours") or reg_hours
    opening_hours = format_weekday_opening_hours(raw_hours)

    raw_summary = (p.get("editorialSummary") or {}).get("text", "") if isinstance(p.get("editorialSummary"), dict) else ""
    editorial_summary = strip_emojis(raw_summary)
    
    reviews_val = p.get("reviews")
    reviews_text = ""
    if isinstance(reviews_val, list):
        reviews_text = strip_emojis(" ".join([r.get("text", {}).get("text", "") for r in reviews_val[:3] if isinstance(r, dict)]))

    # Collect photo URLs (API key is intentionally NOT embedded here;
    # it is appended at download time so keys never land in temp files/reports)
    photos = p.get("photos", []) if isinstance(p.get("photos"), list) else []
    photo_urls = []
    for ph in photos[:3]:
        ph_name = ph.get("name")
        if ph_name:
            photo_urls.append(f"https://places.googleapis.com/v1/{ph_name}/media?maxHeightPx=600&maxWidthPx=600")

    # Determine region dynamically from addressComponents or address text
    region = default_region
    addr_components = p.get("addressComponents") or p.get("address_components") or []
    extracted_locality = ""
    if isinstance(addr_components, list):
        for comp in addr_components:
            types = comp.get("types", [])
            if any(t in types for t in ("locality", "sublocality", "sublocality_level_1", "postal_town", "neighborhood")):
                extracted_locality = comp.get("longText") or comp.get("shortText") or ""
                if extracted_locality:
                    break

    addr_lower = address.lower()
    if extracted_locality:
        region = extracted_locality
    else:
        for keyword, label in REGION_KEYWORDS.items():
            if keyword in addr_lower:
                region = label
                break
        else:
            # Fallback: extract municipality from comma-separated address parts
            parts = [pt.strip() for pt in address.split(",") if pt.strip()]
            if len(parts) >= 3:
                # e.g., "5000 Hwy 7, Markham, ON" -> "Markham"
                candidate_part = parts[-3] if len(parts) >= 4 else parts[1]
                # Strip digits/unit
                cleaned_part = re.sub(r"^\d+\s*", "", candidate_part).strip()
                if cleaned_part and len(cleaned_part) < 30:
                    region = cleaned_part

    business_status = (p.get("businessStatus") or p.get("business_status") or "OPERATIONAL").strip()

    return {
        "name": name,
        "region": region,
        "rating": rating,
        "reviews": reviews_count,
        "business_status": business_status,
        "openingHours": opening_hours,
        "rawOpeningHours": weekday_desc or p.get("openingHours"),
        "regularOpeningHours": reg_hours,
        "regular_opening_hours": reg_hours,
        "address": address,
        "phone": phone,
        "website": website,
        "mapsUrl": maps_url,
        "primaryType": primary_type,
        "editorialSummary": editorial_summary,
        "reviews_text": reviews_text,
        "photo_urls": photo_urls,
        "latitude": lat,
        "longitude": lng,
        "placeId": place_id
    }

class PlacesSearcher:
    transform_google_place = staticmethod(transform_google_place)

    def __init__(self, api_key: Optional[str] = None, referer: Optional[str] = None,
                 cache: Optional["PlaceCache"] = None, max_pages: int = 3,
                 language: str = "en"):
        self.api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        self.referer = (referer or os.environ.get("GOOGLE_MAPS_API_REFERER", "")).strip()
        self.cache = cache
        self.language = (language or "en").strip() or "en"
        # Max Text Search pages (20 results each) fetched per keyword query.
        # Higher values cover dense districts better; every page is a billed request.
        self.max_pages = max(1, int(max_pages or 3))

    def is_live_api_ready(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("YOUR_"))

    def search_places_api(
        self,
        text_query: str,
        lat: float,
        lng: float,
        radius_meters: int = 5000,
        page_token: str = "",
        included_type: Optional[str] = None,
        region_code: Optional[str] = None
    ) -> Dict:
        if not self.is_live_api_ready():
            raise ValueError("Google Maps API Key 未配置或无效。请先配置有效的 API Key: python3 scripts/main.py --configure --set-api-key <KEY>")

        url = "https://places.googleapis.com/v1/places:searchText"
        payload = {
            "textQuery": text_query,
            "maxResultCount": 20,
            "languageCode": self.language,
            "locationBias": {
                "circle": {
                    "center": {
                        "latitude": lat,
                        "longitude": lng
                    },
                    "radius": float(radius_meters)
                }
            }
        }
        if included_type:
            payload["includedType"] = included_type
        if region_code:
            payload["regionCode"] = region_code
        if page_token:
            payload["pageToken"] = page_token

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Goog-Api-Key", self.api_key)
        if self.referer:
            req.add_header("Referer", self.referer)
        field_mask = (
            "places.id,places.displayName,places.formattedAddress,places.location,"
            "places.regularOpeningHours,places.currentOpeningHours,places.businessStatus,"
            "places.primaryType,places.editorialSummary,places.reviews,places.nationalPhoneNumber,"
            "places.internationalPhoneNumber,places.rating,places.userRatingCount,places.websiteUri,"
            "places.googleMapsUri,places.photos,places.addressComponents"
        )
        req.add_header("X-Goog-FieldMask", field_mask)
        req.add_header("Accept-Encoding", "gzip, deflate")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw_data = resp.read()
                    headers_obj = getattr(resp, "headers", None)
                    encoding = headers_obj.get("Content-Encoding", "") if headers_obj and hasattr(headers_obj, "get") else ""
                    if str(encoding).lower() == "gzip":
                        raw_data = gzip.decompress(raw_data)
                    return json.loads(raw_data.decode("utf-8"))
            except urllib.error.HTTPError as e:
                # Retry transient server errors and rate limiting with exponential backoff
                if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    time.sleep(1.5 * (2 ** attempt))
                    continue
                raise
            except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError) as err:
                if attempt == max_retries - 1:
                    raise err
                time.sleep(0.8 * (attempt + 1))

    def search_corridor_probes(
        self,
        probe_points: List[Tuple[float, float]],
        keywords: Optional[List[str]] = None,
        radius_meters: int = 5000,
        max_per_probe: int = 40,
        target_count: int = 30,
        place_types: Optional[List[str]] = None,
        region_code: Optional[str] = None
    ) -> List[Dict]:
        """
        Executes multi-hop corridor probing across a sequence of probe centers using Google Places API.
        Returns deduplicated candidate places in pure English format.
        """
        if not self.is_live_api_ready():
            raise RuntimeError("Google Maps API Key 未配置或无效。请先配置有效的 API Key: python3 scripts/main.py --configure --set-api-key <KEY>")

        places_map = {}
        print(f"Querying Google Places API (New) across {len(probe_points)} corridor probes in English...")

        # Determine search queries from keywords or place_types
        if keywords and any(k.strip() for k in keywords):
            selected_keywords = [k.strip() for k in keywords if k.strip()][:4]
        elif place_types and any(t.strip() for t in place_types):
            selected_keywords = [t.strip().replace("_", " ") for t in place_types if t.strip()][:4]
        else:
            selected_keywords = ["point of interest"]

        primary_type_filter = place_types[0].strip() if (place_types and len(place_types) == 1) else None
        min_pool_target = max(100, target_count * 4)
        map_lock = threading.Lock()

        def query_keyword_paginated(kw: str, p_lat: float, p_lng: float) -> int:
            """Runs one keyword query with pagination, backed by the local cache.

            Fetches up to self.max_pages pages (20 results each), transforming
            and deduplicating into places_map as pages arrive. Stops early when
            a page contributes zero new place IDs (dedupe saturation) so billed
            requests aren't burned on duplicate pages. Results are cached only
            when pagination ran to its natural end -- a truncated run never
            poisons the cache for later runs. Returns new-candidate count.
            """
            def _ingest(raw_places: List[Dict]) -> int:
                added = 0
                for p in raw_places:
                    pid = p.get("id")
                    if not pid:
                        continue
                    with map_lock:
                        if pid not in places_map:
                            places_map[pid] = transform_google_place(
                                p, matched_term=kw, api_key=self.api_key)
                            added += 1
                return added

            cache_key = ""
            if self.cache is not None and PlaceCache is not None:
                cache_key = PlaceCache.search_key(
                    kw, p_lat, p_lng, radius_meters, primary_type_filter,
                    self.language, region_code, self.max_pages,
                )
                hit = self.cache.get_search(cache_key)
                if hit is not None:
                    n = _ingest(hit)
                    print(f"    [Cache] '{kw}' @ ({p_lat:.4f}, {p_lng:.4f}) -> {len(hit)} places, {n} new (0 API calls)")
                    return n

            new_total = 0
            raw_for_cache: List[Dict] = []
            page_token = ""
            early_stopped = False
            for page_no in range(1, self.max_pages + 1):
                data = self.search_places_api(
                    kw,
                    p_lat,
                    p_lng,
                    radius_meters=radius_meters,
                    page_token=page_token,
                    included_type=primary_type_filter,
                    region_code=region_code
                )
                places = data.get("places", [])
                page_new = _ingest(places)
                new_total += page_new
                if cache_key:
                    raw_for_cache.extend(places)
                page_token = data.get("nextPageToken", "")
                if not page_token:
                    break
                if page_no > 1 and page_new == 0:
                    early_stopped = True
                    break
                time.sleep(1.2)  # page tokens need a short warm-up delay
            if cache_key and not early_stopped:
                self.cache.put_search(cache_key, raw_for_cache)
            return new_total

        for idx, (p_lat, p_lng) in enumerate(probe_points):
            print(f"  Probe #{idx + 1}/{len(probe_points)} at ({p_lat:.4f}, {p_lng:.4f}) radius {radius_meters}m...")
            probe_new_count = 0

            # Keyword queries within a probe are independent -> run in parallel.
            # Each worker ingests its pages straight into places_map (under lock),
            # so dedupe saturation is visible across keywords mid-probe.
            with ThreadPoolExecutor(max_workers=min(4, len(selected_keywords))) as pool:
                futures = {
                    pool.submit(query_keyword_paginated, kw, p_lat, p_lng): kw
                    for kw in selected_keywords
                }
                for fut, kw in futures.items():
                    try:
                        probe_new_count += fut.result()
                    except Exception as e:
                        print(f"    [Warning] API Query '{kw}': {e}")

            print(f"  Probe #{idx + 1} found {probe_new_count} new candidates (Total pooled: {len(places_map)})")
            if len(places_map) >= min_pool_target and idx >= 3:
                print(f"  已在走廊内检索到充沛商户 ({len(places_map)} 家)，探测完毕。")
                break

        return list(places_map.values())
