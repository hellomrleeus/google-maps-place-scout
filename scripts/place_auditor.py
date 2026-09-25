#!/usr/bin/env python3
"""
Agent-Native Multimodal Auditor for Universal Place Verification.
Zero External Vision API Dependency.

Architecture (Cascaded Active Multimodal Evaluation):
1. Tier 1: Jev Decision Client (~typesafe/jev-latest) performs fast typed text triage.
   - High confidence positive (P >= 0.85): instant pass (no image needed).
   - High confidence negative (P <= 0.30): instant reject (no image needed).
2. Tier 2: For ambiguous / borderline candidates (0.30 < P < 0.85):
   - Storefront & venue photos from Google Places are downloaded to the system temporary directory.
   - Ambiguous candidates are exported to pending_agent_audit.json with local photo paths.
   - The executing AI Agent visually inspects the photos using native view_file.
   - Verified decisions in agent_audit_results.json take absolute precedence.
3. Tier 3: Universal criteria & keyword NLP heuristic for autonomous offline fallbacks.
"""

import os
import re
import json
import urllib.request
import tempfile
from typing import Dict, List, Tuple, Optional, Any

try:
    from config_manager import get_temp_dir
except ImportError:
    def get_temp_dir(subfolder: str = "audit") -> str:
        d = os.path.join(tempfile.gettempdir(), "place_scout", subfolder)
        os.makedirs(d, exist_ok=True)
        return d

try:
    from jev_client import JevDecisionClient, classify_jev_tier, JevMatchResult, resolve_jev_api_key
except ImportError:
    JevDecisionClient = None
    classify_jev_tier = None
    JevMatchResult = None
    resolve_jev_api_key = None

def download_candidate_photo(url: str, dest_path: str, timeout: float = 6.0, api_key: str = "") -> bool:
    """Downloads a photo from a URL to a local destination file.

    The Google Maps API key is appended here at download time only, so stored
    photo URLs never contain the key (they are written to temp JSON packets).
    """
    if not url or not dest_path:
        return False
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024:
        return True
    if api_key and "places.googleapis.com" in url and "key=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}key={api_key}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (GoogleMapsPlaceScout/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data) > 500:
                with open(dest_path, "wb") as f:
                    f.write(data)
                return True
    except Exception:
        pass
    return False

class PlaceAuditManager:
    def __init__(
        self,
        audit_dir: Optional[str] = None,
        typesafe_api_key: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
        jev_client: Optional[Any] = None,
        criteria: str = "",
        template: str = "general",
        keywords: Optional[List[str]] = None,
        google_api_key: Optional[str] = None,
        **kwargs
    ):
        self.audit_dir = audit_dir or get_temp_dir("audit")
        self.photos_dir = os.path.join(self.audit_dir, "photos")
        os.makedirs(self.photos_dir, exist_ok=True)
        self.criteria = criteria or ""
        self.template = template or "general"
        self.keywords = keywords or []
        # Google Maps API key used ONLY at photo-download time (never persisted in URLs)
        self.google_api_key = (
            google_api_key
            or kwargs.get("google_maps_api_key")
            or os.environ.get("GOOGLE_MAPS_API_KEY", "")
        ).strip()

        self.pending_file = os.path.join(self.audit_dir, "pending_agent_audit.json")
        self.results_file = os.path.join(self.audit_dir, "agent_audit_results.json")
        self.agent_decisions = {}
        self.ambiguous_candidates = []
        # Cache of Jev evaluate_place results keyed by (placeId, criteria, template).
        # export_pending_audit() and audit_place() run over the same candidate list,
        # so without this cache every place would cost 2x Jev API calls.
        self._jev_cache: Dict[tuple, Any] = {}
        self._load_agent_decisions()

        # Audit breakdown stats for reporting
        self.stats = {
            "total": 0,
            "agent_cached": 0,
            "jev_pass": 0,
            "jev_reject": 0,
            "ambiguous_need_agent": 0,
            "heuristic": 0
        }

        # Initialize Jev Decision Client
        self.jev_client = jev_client
        if self.jev_client is None and JevDecisionClient is not None and resolve_jev_api_key is not None:
            candidate_key = resolve_jev_api_key(
                typesafe_api_key,
                kwargs.get("typesafe_key"),
                kwargs.get("jev_key"),
                openrouter_api_key,
            )
            if candidate_key:
                self.jev_client = JevDecisionClient(api_key=candidate_key)

    def _load_agent_decisions(self):
        """Loads agent-verified multimodal decisions from agent_audit_results.json."""
        if os.path.exists(self.results_file):
            try:
                with open(self.results_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            pid = item.get("placeId") or item.get("id")
                            if pid:
                                self.agent_decisions[pid] = item
                    elif isinstance(data, dict):
                        if "decisions" in data and isinstance(data["decisions"], list):
                            for item in data["decisions"]:
                                pid = item.get("placeId") or item.get("id")
                                if pid:
                                    self.agent_decisions[pid] = item
                        elif "candidates" in data and isinstance(data["candidates"], list):
                            for item in data["candidates"]:
                                pid = item.get("placeId") or item.get("id")
                                if pid:
                                    self.agent_decisions[pid] = item
                        else:
                            for k, v in data.items():
                                if isinstance(v, dict):
                                    item = dict(v)
                                    item.setdefault("placeId", k)
                                    self.agent_decisions[k] = item
                print(f"  [Info] Loaded {len(self.agent_decisions)} agent-verified multimodal decisions from {self.results_file}")
            except Exception as e:
                print(f"  [Notice] Note: Could not parse {self.results_file}: {e}")

    def has_unresolved_audits(self) -> bool:
        """Returns True if there are ambiguous/unverified candidates that still need agent review."""
        return len(self.ambiguous_candidates) > 0

    def _cached_evaluate_place(self, place: Dict) -> Optional[Any]:
        """evaluate_place with per-run memoization (halves Jev API usage)."""
        if not self.jev_client:
            return None
        pid = place.get("placeId") or place.get("id", "")
        cache_key = (pid, self.criteria, self.template)
        if cache_key not in self._jev_cache:
            self._jev_cache[cache_key] = self.jev_client.evaluate_place(
                place, criteria=self.criteria, template=self.template
            )
        return self._jev_cache[cache_key]

    def export_pending_audit(
        self,
        candidates: List[Dict],
        download_images: bool = True
    ) -> Tuple[str, List[Dict]]:
        """
        Filters candidates to identify candidates needing Agent Cognitive & Multimodal verification.
        - When Jev is available: captures borderline cases (0.30 < P < 0.85 or NEED_MULTIMODAL_INSPECTION).
        - When Jev is absent (Zero-JEV Mode): captures all unverified candidates needing domain criteria judgment.
        Downloads venue photos to local disk and exports a prompt-ready self-contained packet.
        """
        os.makedirs(self.photos_dir, exist_ok=True)
        ambiguous_items = []

        for c in candidates:
            pid = c.get("placeId") or c.get("id", "")
            if not pid:
                continue

            # Skip if already verified by agent
            if pid in self.agent_decisions:
                continue

            # Skip if explicitly pinned by user
            if c.get("_is_pinned"):
                continue

            # Check Jev status
            needs_audit = False
            jev_conf = 0.5
            jev_rationale = ""

            if self.jev_client and getattr(self.jev_client, "is_ready", lambda: False)():
                jev_eval = self._cached_evaluate_place(c)
                if jev_eval is not None:
                    _, conf, _, rat = jev_eval[:4]
                    tier_status = getattr(jev_eval, "tier_status", None)
                    if not tier_status and len(jev_eval) >= 5:
                        tier_status = jev_eval[4]

                    jev_conf = conf
                    jev_rationale = rat
                    if tier_status == "NEED_MULTIMODAL_INSPECTION" or (tier_status is None and 0.30 < conf < 0.85):
                        needs_audit = True
            else:
                # Zero-JEV Mode (Host Agent Native Cognitive Reasoning)
                # If specific criteria, keywords, or non-general template are requested,
                # invoke the host agent to evaluate the candidates accurately.
                if self.criteria or self.keywords or (self.template and self.template != "general"):
                    needs_audit = True
                else:
                    ptype = (c.get("primaryType") or "").lower()
                    # Ambiguous when type contains mixed or generic offerings
                    if any(k in ptype for k in ("store", "shop", "service", "center", "hall", "studio", "specialty", "point_of_interest")):
                        needs_audit = True

            if needs_audit:
                photo_urls = c.get("photo_urls", [])
                local_paths = []
                if download_images and photo_urls:
                    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", pid)[-24:]
                    for idx, purl in enumerate(photo_urls[:2]):
                        dest = os.path.join(self.photos_dir, f"{clean_id}_{idx}.jpg")
                        if download_candidate_photo(purl, dest, api_key=self.google_api_key):
                            local_paths.append(dest)

                ambiguous_items.append({
                    "placeId": pid,
                    "name": c.get("name"),
                    "primaryType": c.get("primaryType"),
                    "address": c.get("address"),
                    "jev_confidence": round(jev_conf, 2),
                    "jev_rationale": jev_rationale,
                    "local_photo_paths": local_paths,
                    "photo_urls": photo_urls,
                    "editorialSummary": c.get("editorialSummary", ""),
                    "reviews_excerpt": c.get("reviews_text", "")
                })

        self.ambiguous_candidates = ambiguous_items
        packet = {
            "protocol_version": "1.0",
            "task_description": "请作为资深商业勘测专家，评估以下候选场所是否符合目标准入标准 (请结合场所名称、业态类型、官方简介、精选评价及下载的实拍门面/设施照片进行研判)。",
            "target_criteria": self.criteria or f"Category / Template: {self.template}",
            "candidate_count": len(ambiguous_items),
            "candidates": ambiguous_items,
            "items_to_audit": ambiguous_items,
            "expected_output_schema": {
                "placeId_example": {
                    "is_match": True,
                    "matched_features": ["feature 1", "feature 2"],
                    "rationale": "简要陈述研判依据与实拍照片观察结论"
                }
            },
            "instructions_for_agent": [
                "1. 查验 candidates 中每个场所的名称、简介、评价及 local_photo_paths (实拍照片)。",
                "2. 判断该场所是否真正符合 target_criteria (区分专业核心业态 vs 附带次要业务)。",
                "3. 将最终研判裁决写入同目录下的 agent_audit_results.json (支持字典格式或列表格式)。",
                "4. 写入完成后，请重新执行原命令以闭环生成最终路线！"
            ]
        }
        with open(self.pending_file, "w", encoding="utf-8") as f:
            json.dump(packet, f, ensure_ascii=False, indent=2)

        return self.pending_file

    def audit_place(self, place: Dict) -> Tuple[bool, float, List[str], str]:
        """
        Audits a place against target criteria using Cascaded Active Evaluation:
        1. Agent pre-verified multimodal decisions take absolute precedence.
        2. Tier 1: Jev Decision Model (~typesafe/jev-latest) for fast typed evaluation.
           - If definitive pass (>=0.85): instant pass, no image needed.
           - If definitive reject (<=0.30): instant reject, no image needed.
           - If borderline (0.30 < P < 0.85): marked as ambiguous, uses agent decision if available.
        3. Tier 3: Universal heuristic fallback.
        """
        self.stats["total"] += 1
        pid = place.get("placeId") or place.get("id", "")

        # 1. Agent multimodal decision has highest priority
        if pid in self.agent_decisions:
            self.stats["agent_cached"] += 1
            decision = self.agent_decisions[pid]
            is_match = bool(decision.get("is_match", True))
            features = decision.get("matched_features") or decision.get("features") or ["agent-multimodal-verified"]
            rationale = decision.get("rationale") or decision.get("notes") or "Verified by AI Agent multimodal image inspection"
            return is_match, 1.0, features, rationale

        # 2. TypeSafe Jev Tiered Decision Model
        if self.jev_client and getattr(self.jev_client, "is_ready", lambda: False)():
            jev_eval = self._cached_evaluate_place(place)
            if jev_eval is not None:
                is_match, conf, features, rationale = jev_eval[:4]
                tier_status = getattr(jev_eval, "tier_status", None)
                if not tier_status and len(jev_eval) >= 5:
                    tier_status = jev_eval[4]

                if tier_status == "DEFINITIVE_PASS":
                    self.stats["jev_pass"] += 1
                    return True, conf, features, rationale
                elif tier_status == "DEFINITIVE_REJECT":
                    self.stats["jev_reject"] += 1
                    return False, conf, features, rationale
                else:
                    self.stats["ambiguous_need_agent"] += 1
                    amb_rationale = f"Jev borderline ({int(conf * 100)}%): Pending Agent multimodal image review"
                    return is_match, conf, features, amb_rationale

        # 3. Universal heuristic fallback
        self.stats["heuristic"] += 1
        return self._heuristic_audit(place)

    def _heuristic_audit(self, place: Dict) -> Tuple[bool, float, List[str], str]:
        name = place.get("name", "")
        primary_type = place.get("primaryType", "")
        summary = place.get("editorialSummary") or place.get("generativeSummary", "")
        categories = place.get("categories", [])
        reviews = place.get("reviews_text", "")

        haystack = f"{name} {primary_type} {summary} {' '.join(categories)} {reviews}".lower()

        # Custom keywords or criteria matching
        target_terms = []
        if self.keywords:
            target_terms.extend([kw.lower().strip() for kw in self.keywords if kw.strip()])
        if self.criteria:
            target_terms.extend([term.lower().strip() for term in re.split(r"[,;/\s]+", self.criteria) if len(term.strip()) > 2])

        if target_terms:
            matched = [t for t in target_terms if t in haystack]
            if matched:
                return True, 0.90, list(set(matched)), f"Matched target criteria terms: {', '.join(matched[:3])}"
            if any(t in primary_type.lower() for t in target_terms):
                return True, 0.80, ["category match"], f"Category ({primary_type}) matches requested venue type"
            return False, 0.20, [], f"Did not match target criteria or keywords ({self.template})"

        # If no custom criteria or keywords provided, validate as active operational venue
        p_type_label = primary_type.replace("_", " ").title() if primary_type else "Verified Establishment"
        return True, 0.80, [p_type_label], f"Active place matching searched category ({primary_type or 'place'})"
