from __future__ import annotations

import json
import logging
from typing import Protocol, cast

from services.compliance_profile import ProductProfile

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"

_BOOL_FIELDS = (
    "is_electrical",
    "is_electronic",
    "contains_batteries",
    "is_radio_equipment",
    "is_toy",
    "is_childcare",
    "is_ppe",
    "is_medical",
    "is_medicine",
    "is_food_contact",
    "is_cosmetic",
    "is_chemical",
    "is_textile",
    "is_furniture",
    "is_lighting",
    "is_construction",
    "is_machinery",
    "is_pressure_equipment",
    "is_dpp_category",
)

_HEURISTIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "is_electrical": (
        "electrical",
        "plug",
        "mains",
        "voltage",
        "charger",
        "powered",
    ),
    "is_electronic": (
        "electronic",
        "pcb",
        "circuit",
        "sensor",
        "microcontroller",
        "smart",
        "usb",
    ),
    "contains_batteries": (
        "battery",
        "batteries",
        "rechargeable",
        "lithium",
        "button cell",
    ),
    "is_radio_equipment": (
        "bluetooth",
        "wifi",
        "wi-fi",
        "nfc",
        "rf",
        "wireless",
        "radio",
    ),
    "is_toy": ("toy", "doll", "playset", "lego", "kids toy", "children toy"),
    "is_childcare": (
        "infant",
        "toddler",
        "baby",
        "newborn",
        "stroller",
        "pacifier",
        "crib",
    ),
    "is_ppe": (
        "helmet",
        "goggles",
        "respirator",
        "protective gloves",
        "ppe",
        "high visibility",
    ),
    "is_medical": (
        "medical",
        "diagnostic",
        "sterile",
        "thermometer",
        "blood pressure",
        "clinical",
    ),
    "is_medicine": (
        "medicine",
        "medication",
        "pharmaceutical",
        "relief",
        "treatment",
        "drug",
        "ointment",
        "cream",
        "haemorrhoid",
        "hemorrhoid",
        "lidocaine",
        "active ingredient",
    ),
    "is_food_contact": (
        "food storage",
        "drinkware",
        "cup",
        "plate",
        "utensil",
        "kitchenware",
        "food contact",
    ),
    "is_cosmetic": (
        "cosmetic",
        "skincare",
        "shampoo",
        "lotion",
        "makeup",
        "serum",
    ),
    "is_chemical": (
        "chemical",
        "detergent",
        "solvent",
        "adhesive",
        "cleaner",
        "paint",
        "hazardous",
    ),
    "is_textile": (
        "textile",
        "fabric",
        "garment",
        "clothing",
        "shirt",
        "jacket",
        "footwear",
    ),
    "is_furniture": (
        "furniture",
        "chair",
        "table",
        "sofa",
        "shelving",
        "mattress",
        "cabinet",
    ),
    "is_lighting": (
        "lighting",
        "lamp",
        "luminaire",
        "led",
        "light bulb",
        "strip light",
    ),
    "is_construction": (
        "construction",
        "building material",
        "insulation",
        "flooring",
        "cement",
        "structural",
    ),
    "is_machinery": (
        "machinery",
        "machine",
        "motorized",
        "moving parts",
        "blade",
        "industrial equipment",
    ),
    "is_pressure_equipment": (
        "pressure vessel",
        "compressed gas",
        "boiler",
        "cylinder",
        "pressurized",
    ),
}


class _GeminiResponse(Protocol):
    text: str | None


class _GeminiModel(Protocol):
    def generate_content(self, prompt: str) -> _GeminiResponse: ...


class _GenerativeClient(Protocol):
    def GenerativeModel(self, model_name: str) -> _GeminiModel: ...


def infer_product_profile(
    product_category: str,
    product_description: str,
) -> ProductProfile:
    heuristic_profile = _infer_heuristic_profile(product_category, product_description)

    try:
        from services.auth_manager import get_generative_client
    except ImportError:
        logger.warning("auth_manager unavailable; using heuristic product profile")
        return heuristic_profile

    raw = ""
    try:
        genai = cast(_GenerativeClient, get_generative_client())
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            _build_profile_prompt(product_category, product_description)
        )
        raw = _strip_code_fences((response.text or "").strip())
        parsed_obj = cast(object, json.loads(raw))
        if not isinstance(parsed_obj, dict):
            raise ValueError("Gemini response was not a JSON object")
        parsed_dict = cast(dict[object, object], parsed_obj)
        parsed = {str(key): value for key, value in parsed_dict.items()}

        normalized = _normalize_ai_payload(
            parsed,
            product_category=product_category,
            product_description=product_description,
            fallback_confidence=heuristic_profile.confidence,
        )
        return ProductProfile.from_dict(normalized)
    except FileNotFoundError as exc:
        logger.warning("Google credentials not found for product profiler: %s", exc)
        return heuristic_profile
    except json.JSONDecodeError as exc:
        logger.error("Product profiler JSON parse error: %s\nRaw: %s", exc, raw[:500])
        return heuristic_profile
    except Exception as exc:
        logger.error("Product profiler AI inference failed: %s", exc)
        return heuristic_profile


def _infer_heuristic_profile(
    product_category: str, product_description: str
) -> ProductProfile:
    text = f"{product_category} {product_description}".lower()

    values: dict[str, object] = {
        "product_category": product_category,
        "product_description": product_description,
        "source": "heuristic",
    }

    keyword_hits = 0
    for field_name in _BOOL_FIELDS:
        keywords = _HEURISTIC_KEYWORDS.get(field_name, ())
        matched = any(keyword in text for keyword in keywords)
        values[field_name] = matched
        if matched:
            keyword_hits += 1

    if values["contains_batteries"]:
        values["is_electrical"] = True
        values["is_electronic"] = True

    if values["is_toy"] and any(term in text for term in ("baby", "infant", "toddler")):
        values["is_childcare"] = True

    values["is_dpp_category"] = bool(
        values["contains_batteries"]
        or values["is_textile"]
        or values["is_electronic"]
        or values["is_furniture"]
    )

    active_count = sum(1 for field_name in _BOOL_FIELDS if values.get(field_name))
    if keyword_hits == 0:
        confidence = 0.2
    else:
        confidence = min(0.85, 0.4 + (0.06 * keyword_hits) + (0.02 * active_count))

    values["confidence"] = round(confidence, 2)
    return ProductProfile.from_dict(values)


def _normalize_ai_payload(
    payload: dict[str, object],
    *,
    product_category: str,
    product_description: str,
    fallback_confidence: float,
) -> dict[str, object]:
    normalized: dict[str, object] = {
        "product_category": product_category,
        "product_description": product_description,
        "source": "ai",
    }

    for field_name in _BOOL_FIELDS:
        normalized[field_name] = _coerce_bool(payload.get(field_name, False))

    raw_confidence = payload.get("confidence")
    if isinstance(raw_confidence, (int, float, str)):
        try:
            confidence = float(raw_confidence)
        except ValueError:
            confidence = fallback_confidence
    else:
        confidence = fallback_confidence
    normalized["confidence"] = max(0.0, min(1.0, confidence))

    return normalized


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _strip_code_fences(raw: str) -> str:
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        return "\n".join(lines[1:]).strip()
    return raw


def _build_profile_prompt(product_category: str, product_description: str) -> str:
    return f"""You are an EU/UK product-compliance classifier.

Classify the product into regulatory-trigger boolean flags.

PRODUCT CATEGORY: {product_category or "Not specified"}
PRODUCT DESCRIPTION: {product_description or "Not specified"}

Return valid JSON only (no markdown, no code fences) using exactly this schema:
{{
  "is_electrical": false,
  "is_electronic": false,
  "contains_batteries": false,
  "is_radio_equipment": false,
  "is_toy": false,
  "is_childcare": false,
  "is_ppe": false,
  "is_medical": false,
  "is_medicine": false,
  "is_food_contact": false,
  "is_cosmetic": false,
  "is_chemical": false,
  "is_textile": false,
  "is_furniture": false,
  "is_lighting": false,
  "is_construction": false,
  "is_machinery": false,
  "is_pressure_equipment": false,
  "is_dpp_category": false,
  "confidence": 0.0
}}

RULES:
- Return only JSON object with these keys.
- Set each flag to true only when evidence in category/description supports it.
- Confidence must be a float between 0.0 and 1.0.
"""
