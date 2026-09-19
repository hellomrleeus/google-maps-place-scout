#!/usr/bin/env python3
"""
TypeSafe AI Jev Decision Client for Google Maps Place Scout.
Interfaces with TypeSafe AI System One API (POST https://api.typesafe.ai/v1/systemone)
using the typed decision model `jev-latest`.

Philosophy:
"The model answers narrow, typed questions about the state. Your code owns the workflow."

Provides:
1. Low-level `query_decisions(state, questions)`
2. High-level `evaluate_entity_match(candidate, reference)`
3. High-level `evaluate_place(place, criteria, template)`
4. Graceful fallback when API key is unconfigured or request fails.
"""

import os
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple, List

DEFAULT_JEV_MODEL = "jev-latest"
TYPESAFE_SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_DECISIONS_URL = TYPESAFE_SYSTEMONE_URL

class JevMatchResult(tuple):
    """
    Generalized typed decision result.
    Tuple that unpacks as 4 elements: (is_match, conf, features, rationale)
    while exposing .tier_status, .is_match, .features, and .confidence.
    """
    def __new__(cls, is_match, conf, features, rationale, tier_status="DEFINITIVE_PASS"):
        return super(JevMatchResult, cls).__new__(cls, (is_match, conf, features, rationale))

    def __init__(self, is_match, conf, features, rationale, tier_status="DEFINITIVE_PASS"):
        self.is_match = is_match
        self.confidence = conf
        self.features = features
        self.rationale = rationale
        self.tier_status = tier_status

    def __repr__(self):
        return (
            f"JevMatchResult(is_match={self.is_match}, conf={self.confidence:.2f}, "
            f"features={self.features}, tier_status='{self.tier_status}')"
        )

def classify_jev_tier(
    noul_val: float,
    cat_choice: str = "",
    quality_score: int = 0,
    is_negative_category: Optional[bool] = None
) -> str:
    """
    Classifies candidate into one of three decision tiers:
    1. DEFINITIVE_PASS: Very high confidence positive (>=0.85 or strong category + high tier).
    2. DEFINITIVE_REJECT: Very low confidence (<=0.30 or negative category with 0 tier).
    3. NEED_MULTIMODAL_INSPECTION: Borderline / ambiguous (0.30 < P < 0.85), needs vision inspection.
    """
    if is_negative_category is None:
        is_negative_category = (cat_choice in ("unrelated", "non_target", "non_coffee"))

    if (noul_val >= 0.85 and not is_negative_category) or (
        cat_choice in ("specialty_roaster", "detailing_studio", "strength_gym", "specialized_core", "specialty_dining")
        and quality_score >= 2
        and noul_val >= 0.70
    ):
        return "DEFINITIVE_PASS"
    elif noul_val <= 0.30 or (is_negative_category and quality_score == 0 and noul_val <= 0.40):
        return "DEFINITIVE_REJECT"
    else:
        return "NEED_MULTIMODAL_INSPECTION"

def build_criteria_spec(criteria: str = "", template: str = "general") -> Dict[str, Any]:
    """
    Builds a typed decision specification for Jev System One model based on template or natural language criteria.
    """
    t_lower = (template or "").lower().strip()
    c_lower = (criteria or "").lower().strip()

    if t_lower in ("coffee", "cafe") or "coffee" in c_lower or "cafe" in c_lower:
        crit_desc = criteria or "specialty artisan coffee, pour over, single-origin beans, or craft espresso"
        return {
            "template": "coffee",
            "decision_field": "has_commercial_espresso",
            "criteria_description": crit_desc,
            "system_prompt": f"Evaluate whether this establishment is a specialty coffee shop with commercial espresso and craft drinks matching: {crit_desc}.",
            "field_label": "Coffee Category",
            "noul_instructions": f"Is this establishment a specialty/artisan coffee shop focusing on {crit_desc}?",
            "noul_criteria": {
                "true": "Specialty coffee shop, artisan roaster, or third-wave craft cafe",
                "false": "Generic fast food chain, commercial bakery without specialty coffee, or unrelated business"
            },
            "category_instructions": "What is the primary coffee business category?",
            "category_criteria": {
                "specialty_roaster": "In-house roaster, specialty coffee lab, third-wave coffee bar",
                "artisan_cafe": "Independent cafe serving high-quality espresso and pour-over",
                "commercial_chain": "Commercial chain (e.g. Starbucks, Tim Hortons, Dunkin)",
                "bakery_diner": "Bakery or diner where coffee is purely secondary",
                "non_coffee": "Not a coffee establishment"
            },
            "category_labels": {
                "specialty_roaster": "Specialty Roaster / Lab",
                "artisan_cafe": "Artisan Craft Cafe",
                "commercial_chain": "Commercial Coffee Chain",
                "bakery_diner": "Bakery / Secondary Coffee",
                "non_coffee": "Non-coffee Primary"
            },
            "negative_categories": ["non_coffee", "commercial_chain"],
            "tier_instructions": "What is the coffee craft and specialization tier?",
            "tier_criteria": [
                "Standard/Basic commercial coffee",
                "Craft/Fresh espresso and handcrafted drinks",
                "Master/Specialty single-origin, micro-lot roasting"
            ],
            "evidence_prefix": "Specialty match probability"
        }
    elif t_lower in ("dining", "restaurant") or "dining" in c_lower:
        crit_desc = criteria or "quality sit-down dining or specialized culinary offerings"
        return {
            "template": "dining",
            "decision_field": "matches_culinary_criteria",
            "criteria_description": crit_desc,
            "system_prompt": f"Evaluate whether this establishment matches the dining criteria: {crit_desc}.",
            "field_label": "Dining Category",
            "noul_instructions": f"Does this restaurant specialize in or actively serve: {crit_desc}?",
            "noul_criteria": {
                "true": f"Core menu or cuisine directly features {crit_desc}",
                "false": f"Does not feature or offer {crit_desc}"
            },
            "category_instructions": "What is the primary dining format of this establishment?",
            "category_criteria": {
                "specialty_dining": "Dedicated specialty restaurant / bistro",
                "casual_dining": "Full-service casual dining restaurant",
                "fast_casual": "Fast-casual or quick counter service",
                "unrelated": "Not a dining establishment"
            },
            "category_labels": {
                "specialty_dining": "Specialty Dining",
                "casual_dining": "Casual Dining",
                "fast_casual": "Fast Casual",
                "unrelated": "Unrelated"
            },
            "negative_categories": ["unrelated"],
            "tier_instructions": "What is the culinary quality and specialization level?",
            "tier_criteria": [
                "Basic/Standard commercial",
                "Craft/Handmade fresh cuisine",
                "Premium culinary destination"
            ],
            "evidence_prefix": "Dining match probability"
        }
    elif t_lower in ("auto", "car") or "car" in c_lower or "detailing" in c_lower or "ppf" in c_lower:
        crit_desc = criteria or "auto detailing, paint protection film (PPF), ceramic coating, or car care"
        return {
            "template": "auto",
            "decision_field": "provides_auto_service",
            "criteria_description": crit_desc,
            "system_prompt": f"Evaluate whether this business provides professional auto services matching: {crit_desc}.",
            "field_label": "Auto Service Category",
            "noul_instructions": f"Does this business provide professional services for {crit_desc}?",
            "noul_criteria": {
                "true": "Offers dedicated detailing, coating, film, or specialized car services",
                "false": "General mechanical repair only, used car lot, gas station, or unrelated"
            },
            "category_instructions": "What is the primary auto service category?",
            "category_criteria": {
                "detailing_studio": "Dedicated detailing studio, PPF / vinyl wrap / ceramic coating shop",
                "hand_car_wash": "Premium hand car wash and interior conditioning",
                "general_mechanic": "General automotive mechanical repair, oil change, tire shop",
                "car_dealership": "Car sales dealership or rental lot",
                "unrelated": "Not an auto service business"
            },
            "category_labels": {
                "detailing_studio": "Detailing & Coating Studio",
                "hand_car_wash": "Premium Hand Wash",
                "general_mechanic": "General Mechanical Shop",
                "car_dealership": "Car Dealership",
                "unrelated": "Unrelated"
            },
            "negative_categories": ["unrelated"],
            "tier_instructions": "What is the facility and service specialization level?",
            "tier_criteria": [
                "Basic/Drive-through wash or basic repair",
                "Professional shop with detailing bays",
                "High-end studio with clean room / dust-free bays"
            ],
            "evidence_prefix": "Service match probability"
        }
    elif t_lower in ("fitness", "gym") or "gym" in c_lower or "fitness" in c_lower:
        crit_desc = criteria or "gym, fitness center, weightlifting, or personal training"
        return {
            "template": "fitness",
            "decision_field": "has_fitness_facilities",
            "criteria_description": crit_desc,
            "system_prompt": f"Evaluate whether this establishment has fitness facilities matching: {crit_desc}.",
            "field_label": "Fitness Category",
            "noul_instructions": f"Is this establishment a gym or fitness facility offering {crit_desc}?",
            "noul_criteria": {
                "true": "Active gym, fitness club, strength facility, or training studio",
                "false": "Apparel store, supplement shop, medical rehab only, or unrelated"
            },
            "category_instructions": "What is the primary fitness category?",
            "category_criteria": {
                "strength_gym": "Powerlifting, bodybuilding, barbell strength gym",
                "commercial_fitness": "Full-service commercial gym (cardio, machines, weights)",
                "studio_class": "Boutique studio (CrossFit, Pilates, Yoga, Boxing)",
                "personal_training": "Private personal training studio",
                "unrelated": "Not a fitness facility"
            },
            "category_labels": {
                "strength_gym": "Strength & Barbell Gym",
                "commercial_fitness": "Commercial Fitness Center",
                "studio_class": "Boutique Group Studio",
                "personal_training": "Personal Training Studio",
                "unrelated": "Unrelated"
            },
            "negative_categories": ["unrelated"],
            "tier_instructions": "What is the equipment and facility grade?",
            "tier_criteria": [
                "Basic/Minimal equipment",
                "Standard comprehensive gym facility",
                "Elite/Specialized high-end athletic facility"
            ],
            "evidence_prefix": "Facility match probability"
        }
    else:
        # General / Custom Criteria Spec
        crit_desc = criteria or "the user's specified criteria"
        return {
            "template": "general",
            "decision_field": "target_match",
            "criteria_description": crit_desc,
            "system_prompt": f"Evaluate whether this establishment matches the target criteria: {crit_desc}.",
            "field_label": "Match Category",
            "noul_instructions": f"Does this establishment actively match and provide services/products for: '{crit_desc}'?",
            "noul_criteria": {
                "true": f"Core business directly offers or specializes in {crit_desc}",
                "false": f"Does not offer or only tangentially relates to {crit_desc}"
            },
            "category_instructions": "What is the business operational category?",
            "category_criteria": {
                "specialized_core": f"Specialized core provider of {crit_desc}",
                "general_provider": f"General provider offering {crit_desc} among other services",
                "secondary_offering": "Secondary or occasional offering",
                "unrelated": "Completely unrelated establishment"
            },
            "category_labels": {
                "specialized_core": "Specialized Core Business",
                "general_provider": "General Provider",
                "secondary_offering": "Secondary Offering",
                "unrelated": "Unrelated Establishment"
            },
            "negative_categories": ["unrelated"],
            "tier_instructions": f"What is the specialization or quality tier for {crit_desc}?",
            "tier_criteria": [
                "Basic or incidental",
                "Standard professional",
                "Premium or highly specialized"
            ],
            "evidence_prefix": "Criteria match probability"
        }

class JevDecisionClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_JEV_MODEL,
        api_url: str = TYPESAFE_SYSTEMONE_URL,
        site_url: str = "https://github.com/hellomrleeus/google-maps-place-scout",
        site_name: str = "Google Maps Place Scout",
        timeout: float = 10.0
    ):
        self.api_key = (
            api_key
            or os.environ.get("TYPESAFE_API_KEY")
            or os.environ.get("JEV_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or ""
        ).strip()
        self.model = model or DEFAULT_JEV_MODEL
        self.api_url = (
            os.environ.get("TYPESAFE_API_URL")
            or os.environ.get("JEV_API_URL")
            or api_url
            or TYPESAFE_SYSTEMONE_URL
        )
        self.site_url = site_url
        self.site_name = site_name
        self.timeout = timeout

    def is_ready(self) -> bool:
        """Returns True if a valid-looking TypeSafe / Jev API Key is configured."""
        return bool(self.api_key and not self.api_key.startswith("YOUR_") and len(self.api_key) > 8)

    def query_decisions(
        self,
        state: str,
        questions: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Executes a typed decision request against TypeSafe AI System One endpoint.
        Returns the parsed 'answers' dictionary, or None if unavailable/failed.
        """
        if not self.is_ready():
            return None

        payload = {
            "model": self.model,
            "state": str(state).strip(),
            "questions": questions
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.api_url,
                data=req_data,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                if isinstance(resp_data, dict):
                    return resp_data.get("answers", resp_data)
                return None
        except urllib.error.HTTPError as e:
            # Silently log and gracefully return None for pipeline continuity
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                err_body = str(e)
            print(f"  [Notice] TypeSafe Jev HTTP {e.code} warning: {err_body[:120]}")
            return None
        except Exception as e:
            print(f"  [Notice] TypeSafe Jev network/parse warning: {e}")
            return None

    def evaluate_entity_match(
        self,
        candidate: Dict[str, Any],
        reference: Dict[str, Any]
    ) -> Optional[Tuple[bool, float, str, str]]:
        """
        Uses Jev to determine whether a candidate place matches an excluded reference merchant.
        Returns: (is_match, confidence, rationale, match_type) or None if Jev unavailable.
        """
        c_name = candidate.get("name", "")
        c_addr = candidate.get("address", "")
        c_phone = candidate.get("phone", "")
        c_type = candidate.get("primaryType", "")

        r_name = reference.get("name", "")
        r_addr = reference.get("address", "")
        r_phone = reference.get("phone", "")

        state = (
            f"Candidate Place: Name='{c_name}', Address='{c_addr}', Phone='{c_phone}', Type='{c_type}'.\n"
            f"Reference Exclusion Merchant: Name='{r_name}', Address='{r_addr}', Phone='{r_phone}'."
        )

        questions = {
            "is_same_entity": {
                "type": "noul",
                "instructions": "Is the candidate place the exact same physical store, branch, or business entity as the reference merchant?",
                "criteria": {
                    "true": "Exact same physical business and location, or same branch accounting for name variations / translations",
                    "false": "Different location, different branch of a chain, or unrelated business"
                }
            },
            "relationship": {
                "type": "choice",
                "instructions": "What is the structural relationship between candidate and reference?",
                "criteria": {
                    "same_store": "Exact same physical store/location",
                    "chain_different_branch": "Same brand/franchise chain, but at a different address",
                    "different_business": "Completely different businesses"
                }
            }
        }

        answers = self.query_decisions(state, questions)
        if not answers:
            return None

        noul_val = float((answers.get("is_same_entity") or {}).get("noul", 0.0))
        rel_choice = str((answers.get("relationship") or {}).get("choice", "different_business"))

        # Decision threshold:
        # If Jev says it is the exact same store with high confidence (> 0.70)
        # OR relationship is same_store and noul > 0.50 -> match!
        # If it's a chain but different branch, it should NOT be excluded!
        is_matched = (rel_choice == "same_store" and noul_val >= 0.50) or (noul_val >= 0.75)
        conf = noul_val
        rationale = f"Jev entity evaluation: relationship '{rel_choice}' (same-store probability {int(conf * 100)}%)"

        return is_matched, conf, rationale, rel_choice

    def evaluate_place(
        self,
        place: Dict[str, Any],
        criteria: str = "",
        template: str = "general",
        criteria_spec: Optional[Dict[str, Any]] = None
    ) -> Optional[JevMatchResult]:
        """
        Uses Jev to evaluate whether a place matches target criteria
        based on its name, type, editorial summary, and customer reviews text.
        Returns: JevMatchResult(is_match, conf, features, rationale, tier_status) or None if Jev unavailable.
        """
        spec = criteria_spec or build_criteria_spec(criteria=criteria, template=template)
        name = place.get("name", "")
        primary_type = place.get("primaryType", "")
        categories = ", ".join(place.get("categories", []))
        summary = place.get("editorialSummary") or place.get("generativeSummary", "")
        reviews = place.get("reviews_text", "")

        state = (
            f"Place Name: {name}\n"
            f"Primary Type: {primary_type}\n"
            f"Categories: {categories}\n"
            f"Editorial Summary: {summary}\n"
            f"Customer Reviews Excerpt: {reviews[:350]}"
        )

        questions = {
            "target_match": {
                "type": "noul",
                "instructions": spec["noul_instructions"],
                "criteria": spec["noul_criteria"]
            },
            "category_choice": {
                "type": "choice",
                "instructions": spec["category_instructions"],
                "criteria": spec["category_criteria"]
            },
            "quality_tier": {
                "type": "score",
                "instructions": spec["tier_instructions"],
                "criteria": spec["tier_criteria"]
            }
        }

        answers = self.query_decisions(state, questions)
        if not answers:
            return None

        noul_val = float((answers.get("target_match") or {}).get("noul", 0.0))
        cat_choice = str((answers.get("category_choice") or {}).get("choice", "unrelated"))
        tier_score = int((answers.get("quality_tier") or {}).get("score", 0))

        cat_labels = spec.get("category_labels", {})
        category_label = cat_labels.get(cat_choice, cat_choice)
        negative_categories = spec.get("negative_categories", ["unrelated", "non_target", "non_coffee"])
        is_neg = cat_choice in negative_categories

        tier_status = classify_jev_tier(noul_val, cat_choice, tier_score, is_negative_category=is_neg)
        if tier_status == "DEFINITIVE_PASS":
            is_match = True
        elif tier_status == "DEFINITIVE_REJECT":
            is_match = False
        else:
            is_match = (noul_val >= 0.55 and not is_neg) or (tier_score >= 1)

        conf = noul_val
        features = [category_label]
        prefix = spec.get("evidence_prefix", "Match probability")
        rationale = f"Jev decision: {prefix} {int(conf * 100)}%, category: {category_label}, tier: {tier_score}/2 [{tier_status}]"

        return JevMatchResult(is_match, conf, features, rationale, tier_status=tier_status)


