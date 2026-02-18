"""
Launch State Manager — Per-launch state machine for stage progression tracking.

Manages the lifecycle of a product launch through 4 stages:
  Stage 1: Opportunity Validator
  Stage 2: Compliance Compass
  Stage 3: Risk & Pricing Architect
  Stage 4: Creative Studio
  Stage 5: Launch ready (complete)

All methods accept a psycopg connection for transaction management.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row


# ---------------------------------------------------------------------------
# Stage constants
# ---------------------------------------------------------------------------
STAGE_OPPORTUNITY: int = 1   # Opportunity Validator
STAGE_COMPLIANCE: int = 2    # Compliance Compass
STAGE_PRICING: int = 3       # Risk & Pricing Architect
STAGE_CREATIVE: int = 4      # Creative Studio
STAGE_COMPLETE: int = 5      # Launch ready

# ---------------------------------------------------------------------------
# Pursuit category constants
# ---------------------------------------------------------------------------
PURSUIT_SATURATED: str = "Saturated"
PURSUIT_PROVEN: str = "Proven"
PURSUIT_GOLDMINE: str = "Goldmine"

# ---------------------------------------------------------------------------
# Stage names (for display / summary)
# ---------------------------------------------------------------------------
_STAGE_NAMES: dict[int, str] = {
    STAGE_OPPORTUNITY: "Opportunity Validator",
    STAGE_COMPLIANCE: "Compliance Compass",
    STAGE_PRICING: "Risk & Pricing Architect",
    STAGE_CREATIVE: "Creative Studio",
    STAGE_COMPLETE: "Launch Ready",
}


class LaunchStateManager:
    """
    State machine for managing product launch lifecycle.

    Provides CRUD operations on product_launches rows and enforces
    stage-progression rules so that each stage's prerequisites are
    satisfied before advancing.
    """

    # ------------------------------------------------------------------
    # Stage constants (also available as class attributes)
    # ------------------------------------------------------------------
    STAGE_OPPORTUNITY = STAGE_OPPORTUNITY
    STAGE_COMPLIANCE = STAGE_COMPLIANCE
    STAGE_PRICING = STAGE_PRICING
    STAGE_CREATIVE = STAGE_CREATIVE
    STAGE_COMPLETE = STAGE_COMPLETE

    PURSUIT_SATURATED = PURSUIT_SATURATED
    PURSUIT_PROVEN = PURSUIT_PROVEN
    PURSUIT_GOLDMINE = PURSUIT_GOLDMINE

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_launch(
        self,
        conn: psycopg.Connection,
        source_asin: str,
        source_marketplace: str = "US",
        target_marketplaces: list[str] | None = None,
        product_description: str | None = None,
        product_category: str | None = None,
    ) -> int:
        """
        Insert a new product_launches row and return the launch_id.

        Parameters
        ----------
        conn : psycopg.Connection
            Active database connection.
        source_asin : str
            The US ASIN being evaluated.
        source_marketplace : str
            Source marketplace code (default 'US').
        target_marketplaces : list[str] | None
            Target marketplace codes. Defaults to ['UK','DE','FR','IT','ES'].
        product_description : str | None
            Optional free-text product description.
        product_category : str | None
            Optional product category string.

        Returns
        -------
        int
            The newly created launch_id.
        """
        if target_marketplaces is None:
            target_marketplaces = ["UK", "DE", "FR", "IT", "ES"]

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO launchpad.product_launches
                    (source_asin, source_marketplace, target_marketplaces,
                     product_description, product_category)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING launch_id
                """,
                (
                    source_asin,
                    source_marketplace,
                    target_marketplaces,
                    product_description,
                    product_category,
                ),
            )
            row = cur.fetchone()
            return int(row[0])

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_launch(
        self,
        conn: psycopg.Connection,
        launch_id: int,
    ) -> dict[str, Any] | None:
        """
        Retrieve a launch by ID.

        Parameters
        ----------
        conn : psycopg.Connection
            Active database connection.
        launch_id : int
            The launch to retrieve.

        Returns
        -------
        dict | None
            All columns as a dict, or None if not found.
        """
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT launch_id, source_asin, source_marketplace,
                       target_marketplaces, product_description, product_category,
                       pursuit_score, pursuit_category, current_stage,
                       created_at, updated_at
                FROM launchpad.product_launches
                WHERE launch_id = %s
                """,
                (launch_id,),
            )
            return cur.fetchone()

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_launch(
        self,
        conn: psycopg.Connection,
        launch_id: int,
        **fields: Any,
    ) -> bool:
        """
        Update arbitrary fields on a product_launches row.

        Allowed fields: pursuit_score, pursuit_category, current_stage,
        product_description, product_category, source_asin,
        source_marketplace, target_marketplaces.

        Parameters
        ----------
        conn : psycopg.Connection
            Active database connection.
        launch_id : int
            The launch to update.
        **fields
            Column=value pairs to update.

        Returns
        -------
        bool
            True if a row was updated, False if launch_id not found.

        Raises
        ------
        ValueError
            If no fields are provided or an unknown field is specified.
        """
        _ALLOWED_FIELDS = {
            "pursuit_score",
            "pursuit_category",
            "current_stage",
            "product_description",
            "product_category",
            "source_asin",
            "source_marketplace",
            "target_marketplaces",
        }

        if not fields:
            raise ValueError("At least one field must be provided to update_launch.")

        unknown = set(fields) - _ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"Unknown field(s) for update_launch: {unknown}")

        # Validate current_stage bounds if being set directly
        if "current_stage" in fields:
            stage = fields["current_stage"]
            if not (STAGE_OPPORTUNITY <= stage <= STAGE_COMPLETE):
                raise ValueError(
                    f"current_stage must be between {STAGE_OPPORTUNITY} and "
                    f"{STAGE_COMPLETE}, got {stage}."
                )

        set_clauses = ", ".join(f"{col} = %s" for col in fields)
        values = list(fields.values()) + [launch_id]

        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE launchpad.product_launches
                SET {set_clauses}, updated_at = now()
                WHERE launch_id = %s
                """,
                values,
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Stage advancement
    # ------------------------------------------------------------------

    def advance_stage(
        self,
        conn: psycopg.Connection,
        launch_id: int,
        validate: bool = True,
    ) -> bool:
        """
        Increment current_stage by 1.

        Parameters
        ----------
        conn : psycopg.Connection
            Active database connection.
        launch_id : int
            The launch to advance.
        validate : bool
            If True (default), check stage completion criteria before
            advancing. If False, skip validation and advance unconditionally.

        Returns
        -------
        bool
            True if the stage was advanced, False otherwise (already at
            STAGE_COMPLETE, or validation failed).
        """
        launch = self.get_launch(conn, launch_id)
        if launch is None:
            return False

        current = int(launch["current_stage"])

        # Already at maximum stage
        if current >= STAGE_COMPLETE:
            return False

        if validate:
            can_advance, _ = self.can_advance_stage(conn, launch_id)
            if not can_advance:
                return False

        new_stage = current + 1
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE launchpad.product_launches
                SET current_stage = %s, updated_at = now()
                WHERE launch_id = %s
                """,
                (new_stage, launch_id),
            )
            return cur.rowcount > 0

    def can_advance_stage(
        self,
        conn: psycopg.Connection,
        launch_id: int,
    ) -> tuple[bool, list[str]]:
        """
        Check whether the current stage's completion criteria are met.

        Stage completion criteria
        ------------------------
        Stage 1 (Opportunity Validator):
            - pursuit_score must be set (not NULL)

        Stage 2 (Compliance Compass):
            - No compliance checklist items in 'pending' or 'blocked' status
              (all items must be 'completed' or 'not_applicable' or 'in_progress')
            - At least one compliance item must exist

        Stage 3 (Risk & Pricing Architect):
            - At least one pricing_analysis row must exist for this launch

        Stage 4 (Creative Studio):
            - At least one listing_draft row must exist for this launch

        Stage 5 (Complete):
            - Already complete; cannot advance further.

        Parameters
        ----------
        conn : psycopg.Connection
            Active database connection.
        launch_id : int
            The launch to check.

        Returns
        -------
        tuple[bool, list[str]]
            (can_advance, blockers) where blockers is a list of human-readable
            strings describing what must be completed before advancing.
        """
        launch = self.get_launch(conn, launch_id)
        if launch is None:
            return False, [f"Launch {launch_id} not found."]

        current = int(launch["current_stage"])
        blockers: list[str] = []

        if current >= STAGE_COMPLETE:
            blockers.append("Launch is already complete; no further stages to advance to.")
            return False, blockers

        if current == STAGE_OPPORTUNITY:
            # Stage 1 → 2: needs pursuit_score
            if launch.get("pursuit_score") is None:
                blockers.append(
                    "Stage 1 incomplete: pursuit_score has not been calculated yet."
                )

        elif current == STAGE_COMPLIANCE:
            # Stage 2 → 3: no compliance items in pending/blocked
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE status IN ('pending', 'blocked')) AS blocking
                    FROM launchpad.launch_compliance_checklist
                    WHERE launch_id = %s
                    """,
                    (launch_id,),
                )
                row = cur.fetchone()
                total = int(row[0]) if row else 0
                blocking = int(row[1]) if row else 0

            if total == 0:
                blockers.append(
                    "Stage 2 incomplete: no compliance checklist items exist for this launch."
                )
            elif blocking > 0:
                blockers.append(
                    f"Stage 2 incomplete: {blocking} compliance item(s) are still "
                    f"'pending' or 'blocked' and must be resolved."
                )

        elif current == STAGE_PRICING:
            # Stage 3 → 4: needs at least one pricing_analysis row
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM launchpad.pricing_analysis
                    WHERE launch_id = %s
                    """,
                    (launch_id,),
                )
                row = cur.fetchone()
                count = int(row[0]) if row else 0

            if count == 0:
                blockers.append(
                    "Stage 3 incomplete: no pricing analysis has been generated for this launch."
                )

        elif current == STAGE_CREATIVE:
            # Stage 4 → 5: needs at least one listing_draft row
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM launchpad.listing_drafts
                    WHERE launch_id = %s
                    """,
                    (launch_id,),
                )
                row = cur.fetchone()
                count = int(row[0]) if row else 0

            if count == 0:
                blockers.append(
                    "Stage 4 incomplete: no listing drafts have been generated for this launch."
                )

        can_advance = len(blockers) == 0
        return can_advance, blockers

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_launch_summary(
        self,
        conn: psycopg.Connection,
        launch_id: int,
    ) -> dict[str, Any]:
        """
        Return a comprehensive summary of a launch.

        Includes launch details, current stage name, stage completion
        status, and key metrics.

        Parameters
        ----------
        conn : psycopg.Connection
            Active database connection.
        launch_id : int
            The launch to summarise.

        Returns
        -------
        dict
            Summary dict with keys:
              launch_id, source_asin, source_marketplace,
              target_marketplaces, product_description, product_category,
              current_stage, current_stage_name, created_at, updated_at,
              pursuit_score, pursuit_category,
              can_advance, blockers,
              compliance_progress (dict with total/completed/blocked/pending),
              has_pricing_analysis (bool),
              has_listing_drafts (bool).

        Raises
        ------
        ValueError
            If the launch is not found.
        """
        launch = self.get_launch(conn, launch_id)
        if launch is None:
            raise ValueError(f"Launch {launch_id} not found.")

        current = int(launch["current_stage"])
        can_advance, blockers = self.can_advance_stage(conn, launch_id)

        # Compliance progress
        compliance_progress: dict[str, int] = {
            "total": 0,
            "completed": 0,
            "not_applicable": 0,
            "in_progress": 0,
            "pending": 0,
            "blocked": 0,
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*) AS cnt
                FROM launchpad.launch_compliance_checklist
                WHERE launch_id = %s
                GROUP BY status
                """,
                (launch_id,),
            )
            for row in cur.fetchall():
                status, cnt = row[0], int(row[1])
                if status in compliance_progress:
                    compliance_progress[status] = cnt
                compliance_progress["total"] += cnt

        # Pricing analysis presence
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM launchpad.pricing_analysis WHERE launch_id = %s)",
                (launch_id,),
            )
            has_pricing_analysis: bool = bool(cur.fetchone()[0])

        # Listing drafts presence
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM launchpad.listing_drafts WHERE launch_id = %s)",
                (launch_id,),
            )
            has_listing_drafts: bool = bool(cur.fetchone()[0])

        return {
            "launch_id": launch["launch_id"],
            "source_asin": launch["source_asin"],
            "source_marketplace": launch["source_marketplace"],
            "target_marketplaces": launch["target_marketplaces"],
            "product_description": launch["product_description"],
            "product_category": launch["product_category"],
            "current_stage": current,
            "current_stage_name": _STAGE_NAMES.get(current, f"Stage {current}"),
            "created_at": launch["created_at"],
            "updated_at": launch["updated_at"],
            # Key metrics
            "pursuit_score": launch.get("pursuit_score"),
            "pursuit_category": launch.get("pursuit_category"),
            # Stage completion
            "can_advance": can_advance,
            "blockers": blockers,
            "compliance_progress": compliance_progress,
            "has_pricing_analysis": has_pricing_analysis,
            "has_listing_drafts": has_listing_drafts,
        }

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_launches(
        self,
        conn: psycopg.Connection,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        List launches with optional filtering by pursuit_category.

        Parameters
        ----------
        conn : psycopg.Connection
            Active database connection.
        status : str | None
            Filter by pursuit_category ('Saturated', 'Proven', 'Goldmine').
            Pass None to return all launches.
        limit : int
            Maximum number of rows to return (default 50).

        Returns
        -------
        list[dict]
            List of launch dicts ordered by created_at DESC.
        """
        with conn.cursor(row_factory=dict_row) as cur:
            if status is not None:
                cur.execute(
                    """
                    SELECT launch_id, source_asin, source_marketplace,
                           target_marketplaces, product_description, product_category,
                           pursuit_score, pursuit_category, current_stage,
                           created_at, updated_at
                    FROM launchpad.product_launches
                    WHERE pursuit_category = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (status, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT launch_id, source_asin, source_marketplace,
                           target_marketplaces, product_description, product_category,
                           pursuit_score, pursuit_category, current_stage,
                           created_at, updated_at
                    FROM launchpad.product_launches
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            return list(cur.fetchall())
