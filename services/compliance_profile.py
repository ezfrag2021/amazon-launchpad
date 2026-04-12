"""
Product Profile & Scope Criteria — Compliance Scope Engine models.

ProductProfile:  Boolean flags describing a product's regulatory triggers.
ScopeCriteria:   Mapping from profile flags to a compliance rule's applicability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


# =========================================================================== #
# ProductProfile                                                               #
# =========================================================================== #


@dataclass(frozen=True, slots=True)
class ProductProfile:
    """Product regulatory-trigger flags.  Frozen so a confirmed profile cannot be mutated."""

    # -- Electrical / Electronic ------------------------------------------
    is_electrical: bool = False
    """Product contains mains-powered or low-voltage electrical components.
    Triggers: LVD (2014/35/EU), EMC (2014/30/EU), CE, UKCA."""

    is_electronic: bool = False
    """Product contains electronic circuitry (PCBs, ICs, microcontrollers).
    Triggers: WEEE, RoHS, CE, UKCA."""

    contains_batteries: bool = False
    """Product ships with or contains removable/embedded batteries.
    Triggers: Battery Regulation (EU) 2023/1542, WEEE, transport rules."""

    is_radio_equipment: bool = False
    """Product transmits or receives radio waves (Wi-Fi, Bluetooth, NFC, RF).
    Triggers: RED (2014/53/EU), CE, UKCA."""

    # -- Toy safety -------------------------------------------------------
    is_toy: bool = False
    """Product is designed or intended for use in play by children under 14.
    Triggers: Toy Safety Directive 2009/48/EC, EN 71, CE."""

    # -- Vulnerable populations -------------------------------------------
    is_childcare: bool = False
    """Product is intended for children under 36 months (feeding, sleeping,
    carrying, hygiene).  Triggers: heightened chemical & mechanical safety
    requirements under General Product Safety Regulation."""

    # -- PPE / Medical ----------------------------------------------------
    is_ppe: bool = False
    """Product is Personal Protective Equipment (gloves, helmets, eye
    protection, etc.).  Triggers: PPE Regulation (EU) 2016/425."""

    is_medical: bool = False
    """Product qualifies as a medical device or accessory.
    Triggers: MDR (EU) 2017/745, UKCA medical-device route."""

    is_medicine: bool = False
    """Product is a medicinal product (medication/pharmaceutical).
    Triggers: Directive 2001/83/EC, Regulation (EC) No 726/2004, MHRA/EMA rules."""

    # -- Materials & contact ----------------------------------------------
    is_food_contact: bool = False
    """Product or component comes into contact with food or drink.
    Triggers: FCM Regulation (EC) 1935/2004, national FCM rules."""

    is_cosmetic: bool = False
    """Product is a cosmetic or personal-care product applied to the body.
    Triggers: Cosmetics Regulation (EC) 1223/2009."""

    is_chemical: bool = False
    """Product is or contains a chemical substance/mixture for end-user use.
    Triggers: CLP (EC) 1272/2008, REACH registration/restriction."""

    # -- Category-specific ------------------------------------------------
    is_textile: bool = False
    """Product is a textile, garment, or footwear item.
    Triggers: Textile Labelling Regulation (EU) 1007/2011, REACH Annex XVII,
    DPP (2026 first-wave category)."""

    is_furniture: bool = False
    """Product is furniture (seating, tables, shelving, beds, mattresses).
    Triggers: flammability standards (UK Furniture Regs), DPP (2026)."""

    is_lighting: bool = False
    """Product is a lighting product (lamps, luminaires, LED strips).
    Triggers: Energy Labelling, Ecodesign, WEEE, RoHS."""

    is_construction: bool = False
    """Product is a construction product (insulation, flooring, fasteners).
    Triggers: CPR (EU) 305/2011."""

    is_machinery: bool = False
    """Product is machinery with moving parts posing injury risk.
    Triggers: Machinery Regulation (EU) 2023/1230 (replaces 2006/42/EC)."""

    is_pressure_equipment: bool = False
    """Product is pressure equipment (vessels, piping, safety accessories).
    Triggers: PED 2014/68/EU."""

    # -- Cross-cutting ----------------------------------------------------
    is_dpp_category: bool = False
    """Product falls under an EU Digital Product Passport (2026+) scope
    category (batteries, textiles, electronics, furniture, tyres, etc.).
    Triggers: ESPR / DPP Regulation."""

    # -- Metadata (not regulatory flags) ----------------------------------
    product_category: str = ""
    """Original product category string from the launch record."""

    product_description: str = ""
    """Product description text used during profiling (audit trail)."""

    confidence: float = 1.0
    """Profiler confidence score (0.0–1.0).  Manual overrides are 1.0."""

    source: str = "manual"
    """How this profile was generated: 'ai', 'heuristic', or 'manual'."""

    # -- Helpers -----------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-safe for DB/session storage)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductProfile:
        """Construct from a dict, silently ignoring unknown keys for forward-compat."""
        valid_names = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_names}
        return cls(**filtered)

    @property
    def active_flags(self) -> list[str]:
        """Names of all boolean flags currently set to True."""
        return [
            f.name for f in fields(self) if f.type == "bool" and getattr(self, f.name)
        ]

    @property
    def flag_count(self) -> int:
        """Number of active (True) boolean flags."""
        return len(self.active_flags)


# =========================================================================== #
# ScopeCriteria                                                                #
# =========================================================================== #


@dataclass(frozen=True, slots=True)
class ScopeCriteria:
    """Maps ProductProfile flags → rule applicability via AND/OR/NOT logic.

    matches() returns True when:
      ALL required_flags are True AND
      at least ONE any_flags is True (vacuous if empty) AND
      NONE of exclude_flags are True.
    """

    regime: str
    """Compliance regime this criteria maps to (e.g. 'CE', 'WEEE')."""

    required_flags: tuple[str, ...] = ()
    """ALL of these ProductProfile flags must be True (AND logic)."""

    any_flags: tuple[str, ...] = ()
    """At least ONE of these ProductProfile flags must be True (OR logic).
    If empty, this condition is considered satisfied."""

    exclude_flags: tuple[str, ...] = ()
    """NONE of these ProductProfile flags may be True (NOT logic).
    If any listed flag is True, the criteria does NOT match."""

    rule_id: int | None = None
    """Optional FK to compliance_rules.rule_id for rule-level granularity.
    When None, the criteria applies to all rules of the given regime."""

    description: str = ""
    """Human-readable explanation of why this scope condition exists."""

    # -- Evaluation --------------------------------------------------------

    def matches(self, profile: ProductProfile) -> bool:
        # 1. AND — all required flags must be set
        for flag in self.required_flags:
            if not getattr(profile, flag, False):
                return False

        # 2. OR — at least one must be set (skip if empty)
        if self.any_flags:
            if not any(getattr(profile, flag, False) for flag in self.any_flags):
                return False

        # 3. NOT — none may be set
        for flag in self.exclude_flags:
            if getattr(profile, flag, False):
                return False

        return True

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScopeCriteria:
        """Construct from a dict, coercing list→tuple for flag fields."""
        valid_names = {f.name for f in fields(cls)}
        filtered: dict[str, Any] = {}
        for k, v in data.items():
            if k not in valid_names:
                continue
            # Convert lists to tuples for frozen dataclass compatibility
            if k in ("required_flags", "any_flags", "exclude_flags") and isinstance(
                v, list
            ):
                filtered[k] = tuple(v)
            else:
                filtered[k] = v
        return cls(**filtered)
