"""
Compliance Engine — Stage 2: Compliance Compass
CE/UKCA/WEEE/RoHS/ToyEN71/DPP rules engine for Amazon Launchpad.

Matches products against compliance_rules rows, generates checklists,
and tracks compliance progress. No DB queries — pure logic layer.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


class ComplianceEngine:
    # ------------------------------------------------------------------ #
    # Regime constants                                                     #
    # ------------------------------------------------------------------ #
    REGIME_CE = "CE"
    REGIME_UKCA = "UKCA"
    REGIME_WEEE = "WEEE"
    REGIME_ROHS = "RoHS"
    REGIME_TOY_EN71 = "ToyEN71"
    REGIME_DPP = "DPP"

    ALL_REGIMES: list[str] = [
        REGIME_CE,
        REGIME_UKCA,
        REGIME_WEEE,
        REGIME_ROHS,
        REGIME_TOY_EN71,
        REGIME_DPP,
    ]

    # ------------------------------------------------------------------ #
    # Status constants — must match DB CHECK constraint                   #
    # ------------------------------------------------------------------ #
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_NOT_APPLICABLE = "not_applicable"
    STATUS_BLOCKED = "blocked"

    # ------------------------------------------------------------------ #
    # DPP-relevant product categories (EU Digital Product Passport 2026)  #
    # ------------------------------------------------------------------ #
    _DPP_CATEGORY_KEYWORDS: list[str] = [
        # Electronics / batteries
        "electronics", "electrical", "battery", "batteries", "charger",
        "smartphone", "tablet", "laptop", "computer", "headphone", "speaker",
        "camera", "tv", "television", "monitor", "printer", "appliance",
        # Textiles
        "textile", "clothing", "apparel", "garment", "fabric", "fashion",
        "shirt", "trousers", "dress", "jacket", "shoes", "footwear",
        # Furniture
        "furniture", "chair", "table", "sofa", "desk", "shelf", "cabinet",
        # Construction / building materials
        "construction", "building material", "insulation", "flooring",
        # Tyres
        "tyre", "tire",
        # Detergents / chemicals
        "detergent", "chemical", "cleaning product",
        # Toys (already covered by ToyEN71 but DPP also applies)
        "toy",
    ]

    # ------------------------------------------------------------------ #
    # Rule matching                                                        #
    # ------------------------------------------------------------------ #

    def match_rules_for_product(
        self,
        product_category: str,
        product_attributes: dict[str, Any],
        rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return rules whose category_pattern matches the product.

        Matching strategy (in order):
        1. Treat category_pattern as a regex (case-insensitive).
        2. Fall back to plain substring match if the pattern is not valid regex.

        Args:
            product_category: Human-readable category string, e.g. "Electronic Toys".
            product_attributes: Arbitrary product metadata dict (HS code, material, etc.).
            rules: Rows from launchpad.compliance_rules.

        Returns:
            Subset of *rules* that apply to this product, each dict unchanged.
        """
        matched: list[dict[str, Any]] = []
        search_text = self._build_search_text(product_category, product_attributes)

        for rule in rules:
            pattern: str = rule.get("category_pattern", "")
            if not pattern:
                continue
            if self._pattern_matches(pattern, search_text):
                matched.append(rule)

        return matched

    # ------------------------------------------------------------------ #
    # Checklist generation                                                 #
    # ------------------------------------------------------------------ #

    def generate_checklist(
        self,
        launch_id: int,
        product_category: str,
        product_attributes: dict[str, Any],
        rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Generate checklist items ready for insertion into launch_compliance_checklist.

        Args:
            launch_id: FK to product_launches.launch_id.
            product_category: Product category string.
            product_attributes: Arbitrary product metadata.
            rules: All compliance_rules rows (engine will filter applicable ones).

        Returns:
            List of dicts with keys:
                launch_id, rule_id, status, regime, requirement_name,
                requirement_description, documentation_required,
                is_2026_dpp_relevant, evidence_url, notes, completed_at
        """
        applicable_rules = self.match_rules_for_product(
            product_category, product_attributes, rules
        )
        dpp_relevant = self.is_dpp_relevant(product_category, product_attributes)

        checklist: list[dict[str, Any]] = []
        for rule in applicable_rules:
            # Override is_2026_dpp_relevant if product-level DPP flag is set
            rule_dpp = rule.get("is_2026_dpp_relevant", False)
            effective_dpp = rule_dpp or (
                dpp_relevant and rule.get("regime") == self.REGIME_DPP
            )

            item: dict[str, Any] = {
                "launch_id": launch_id,
                "rule_id": rule["rule_id"],
                "status": self.STATUS_PENDING,
                # Denormalised fields for UI convenience (not stored in DB)
                "regime": rule.get("regime"),
                "requirement_name": rule.get("requirement_name"),
                "requirement_description": rule.get("requirement_description"),
                "documentation_required": self.get_required_documents(rule),
                "is_2026_dpp_relevant": effective_dpp,
                "effective_date": rule.get("effective_date"),
                "source_url": rule.get("source_url"),
                # DB columns with defaults
                "evidence_url": None,
                "notes": None,
                "completed_at": None,
            }
            checklist.append(item)

        # Sort: DPP items last (they are future-dated), then by regime, then name
        checklist.sort(
            key=lambda x: (
                x["is_2026_dpp_relevant"],
                x["regime"] or "",
                x["requirement_name"] or "",
            )
        )
        return checklist

    # ------------------------------------------------------------------ #
    # DPP relevance                                                        #
    # ------------------------------------------------------------------ #

    def is_dpp_relevant(
        self,
        product_category: str,
        attributes: dict[str, Any],
    ) -> bool:
        """Return True if the product falls under EU Digital Product Passport (2026).

        Checks product_category and selected attribute keys against a keyword list.
        """
        search_text = self._build_search_text(product_category, attributes).lower()
        return any(kw in search_text for kw in self._DPP_CATEGORY_KEYWORDS)

    # ------------------------------------------------------------------ #
    # Document extraction                                                  #
    # ------------------------------------------------------------------ #

    def get_required_documents(self, rule: dict[str, Any]) -> list[str]:
        """Extract the documentation_required array from a rule row.

        Handles None, empty list, and PostgreSQL TEXT[] representations.
        """
        docs = rule.get("documentation_required")
        if not docs:
            return []
        if isinstance(docs, list):
            return [str(d) for d in docs if d]
        # Fallback: comma-separated string
        if isinstance(docs, str):
            return [d.strip() for d in docs.split(",") if d.strip()]
        return []

    # ------------------------------------------------------------------ #
    # Progress calculation                                                 #
    # ------------------------------------------------------------------ #

    def calculate_compliance_progress(
        self,
        checklist_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate checklist status counts and completion percentage.

        Args:
            checklist_items: List of checklist dicts (each must have a "status" key).

        Returns:
            Dict with keys:
                total, completed, in_progress, pending, blocked,
                not_applicable, completion_pct (float 0–100)
        """
        counts: dict[str, int] = {
            self.STATUS_COMPLETED: 0,
            self.STATUS_IN_PROGRESS: 0,
            self.STATUS_PENDING: 0,
            self.STATUS_BLOCKED: 0,
            self.STATUS_NOT_APPLICABLE: 0,
        }

        for item in checklist_items:
            status = item.get("status", self.STATUS_PENDING)
            if status in counts:
                counts[status] += 1
            else:
                counts[self.STATUS_PENDING] += 1  # treat unknown as pending

        total = len(checklist_items)
        # Actionable items exclude not_applicable
        actionable = total - counts[self.STATUS_NOT_APPLICABLE]
        completion_pct = (
            round(counts[self.STATUS_COMPLETED] / actionable * 100, 1)
            if actionable > 0
            else 0.0
        )

        return {
            "total": total,
            "completed": counts[self.STATUS_COMPLETED],
            "in_progress": counts[self.STATUS_IN_PROGRESS],
            "pending": counts[self.STATUS_PENDING],
            "blocked": counts[self.STATUS_BLOCKED],
            "not_applicable": counts[self.STATUS_NOT_APPLICABLE],
            "completion_pct": completion_pct,
        }

    # ------------------------------------------------------------------ #
    # Next action recommendation                                           #
    # ------------------------------------------------------------------ #

    def get_next_action(self, checklist_items: list[dict[str, Any]]) -> str:
        """Return a human-readable recommended next action.

        Priority order: blocked → in_progress → pending → (all done).
        """
        blocked = [
            i for i in checklist_items if i.get("status") == self.STATUS_BLOCKED
        ]
        in_progress = [
            i for i in checklist_items if i.get("status") == self.STATUS_IN_PROGRESS
        ]
        pending = [
            i for i in checklist_items if i.get("status") == self.STATUS_PENDING
        ]

        if blocked:
            names = self._item_names(blocked[:2])
            extra = f" (+{len(blocked) - 2} more)" if len(blocked) > 2 else ""
            return (
                f"Resolve {len(blocked)} blocked item(s): {names}{extra}. "
                "Check notes for blockers and upload missing evidence."
            )

        if in_progress:
            names = self._item_names(in_progress[:2])
            extra = f" (+{len(in_progress) - 2} more)" if len(in_progress) > 2 else ""
            return (
                f"Continue {len(in_progress)} in-progress item(s): {names}{extra}."
            )

        if pending:
            # Suggest starting with the first non-DPP pending item if available
            non_dpp = [
                i for i in pending if not i.get("is_2026_dpp_relevant", False)
            ]
            first = non_dpp[0] if non_dpp else pending[0]
            name = first.get("requirement_name", "next requirement")
            regime = first.get("regime", "")
            regime_str = f" [{regime}]" if regime else ""
            return (
                f"Start with: {name}{regime_str}. "
                f"{len(pending)} pending item(s) remaining."
            )

        # Check if everything is completed or not_applicable
        progress = self.calculate_compliance_progress(checklist_items)
        if progress["total"] == 0:
            return "No compliance requirements found for this product."
        if progress["blocked"] == 0 and progress["pending"] == 0 and progress["in_progress"] == 0:
            return "All compliance requirements completed. Ready to proceed to Stage 3."

        return "Review checklist items and update their status."

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _build_search_text(
        self,
        product_category: str,
        product_attributes: dict[str, Any],
    ) -> str:
        """Combine category and selected attribute values into one searchable string."""
        parts = [product_category or ""]
        # Include string-valued attributes that may carry category signals
        for key, value in (product_attributes or {}).items():
            if isinstance(value, str):
                parts.append(value)
        return " ".join(parts)

    @staticmethod
    def _pattern_matches(pattern: str, text: str) -> bool:
        """Return True if *pattern* matches *text* (regex or substring, case-insensitive)."""
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except re.error:
            # Invalid regex — fall back to plain substring match
            return pattern.lower() in text.lower()

    @staticmethod
    def _item_names(items: list[dict[str, Any]]) -> str:
        """Return a comma-joined string of requirement names for display."""
        return ", ".join(
            f'"{i.get("requirement_name", "Unknown")}"' for i in items
        )
