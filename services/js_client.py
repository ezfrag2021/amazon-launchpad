"""
Jungle Scout API client wrapper for amazon-launchpad.

Wraps the junglescout-client library with budget metering against
launchpad.api_call_ledger — completely independent from amazon-mi's
market_intel.api_call_ledger.

CRITICAL: This module MUST NEVER touch market_intel.api_call_ledger.
          All budget tracking goes to launchpad.api_call_ledger only.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


class BudgetExhaustedError(Exception):
    """Raised when the Launchpad monthly API budget is exhausted."""

    def __init__(self, remaining: int, requested: int) -> None:
        self.remaining = remaining
        self.requested = requested
        super().__init__(
            f"Launchpad API budget exhausted: {remaining} pages remaining, "
            f"{requested} requested. Check launchpad.budget_config."
        )


class JungleScoutClient:
    """Jungle Scout API client with Launchpad budget metering.

    Wraps ``junglescout_client`` and enforces the monthly hard cap stored in
    ``launchpad.budget_config``.  Every API call is recorded in
    ``launchpad.api_call_ledger`` — never in ``market_intel.api_call_ledger``.

    Usage::

        from services.js_client import JungleScoutClient, BudgetExhaustedError

        client = JungleScoutClient()
        with psycopg.connect(dsn) as conn:
            products = client.get_product_database(conn, "UK", min_monthly_revenue=5000)
    """

    def __init__(self) -> None:
        """Initialise the Jungle Scout client from environment variables.

        Required env vars:
            JUNGLESCOUT_API_KEY_NAME: API key name / account identifier.
            JUNGLESCOUT_API_KEY:      Secret API key.

        Raises:
            RuntimeError: If either required env var is missing.
            ImportError:  If ``junglescout_client`` is not installed.
        """
        api_key_name = os.getenv("JUNGLESCOUT_API_KEY_NAME")
        api_key = os.getenv("JUNGLESCOUT_API_KEY")

        if not api_key_name:
            raise RuntimeError(
                "JUNGLESCOUT_API_KEY_NAME environment variable is not set."
            )
        if not api_key:
            raise RuntimeError(
                "JUNGLESCOUT_API_KEY environment variable is not set."
            )

        try:
            from junglescout import Client as _JSClient  # type: ignore[import]
            from junglescout.models.params import ApiType  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "junglescout-client is not installed. "
                "Run: pip install 'junglescout-client>=0.6,<1'"
            ) from exc

        self._client = _JSClient(
            api_key_name=api_key_name,
            api_key=api_key,
            marketplace="US",  # default; overridden per-call via marketplace param
        )
        self._ApiType = ApiType
        logger.debug("JungleScoutClient initialised (key_name=%s)", api_key_name)

    # ------------------------------------------------------------------
    # Budget helpers
    # ------------------------------------------------------------------

    def get_budget_status(self, conn: psycopg.Connection) -> dict[str, Any]:
        """Return current budget info from ``launchpad.v_api_budget_status``.

        Args:
            conn: Active psycopg connection to the launchpad database.

        Returns:
            Dict with keys: month_start, total_billable_pages, monthly_hard_cap,
            remaining_budget, allow_override, override_reason.
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    month_start,
                    total_billable_pages,
                    monthly_hard_cap,
                    remaining_budget,
                    allow_override,
                    override_reason
                FROM launchpad.v_api_budget_status
                """
            )
            row = cur.fetchone()

        if row is None:
            raise RuntimeError(
                "launchpad.v_api_budget_status returned no rows. "
                "Ensure launchpad.budget_config has exactly one row (id=1)."
            )

        return {
            "month_start": row[0],
            "total_billable_pages": row[1],
            "monthly_hard_cap": row[2],
            "remaining_budget": row[3],
            "allow_override": row[4],
            "override_reason": row[5],
        }

    def get_remaining_calls(self, conn: psycopg.Connection) -> int:
        """Return the number of remaining billable pages for the current month.

        Args:
            conn: Active psycopg connection to the launchpad database.

        Returns:
            Remaining budget as an integer (may be negative if overridden).
        """
        status = self.get_budget_status(conn)
        return int(status["remaining_budget"])

    def check_budget_available(
        self, conn: psycopg.Connection, pages: int = 1
    ) -> bool:
        """Check whether *pages* billable pages are available in the budget.

        Respects ``allow_override`` — if the override flag is set in
        ``launchpad.budget_config`` the check always returns ``True``.

        Args:
            conn:  Active psycopg connection.
            pages: Number of billable pages to check for (default 1).

        Returns:
            ``True`` if the budget can accommodate *pages*, ``False`` otherwise.
        """
        status = self.get_budget_status(conn)
        if status["allow_override"]:
            logger.warning(
                "Budget override is active (reason: %s). Bypassing cap check.",
                status["override_reason"],
            )
            return True
        return int(status["remaining_budget"]) >= pages

    def reserve_budget(
        self,
        conn: psycopg.Connection,
        script_name: str,
        endpoint: str,
        marketplace: str | None = None,
        pages: int = 1,
        launch_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Check budget and, if available, record the call in the ledger.

        Inserts a row into ``launchpad.api_call_ledger`` (never into
        ``market_intel.api_call_ledger``).

        Args:
            conn:        Active psycopg connection.
            script_name: Name of the calling script / module.
            endpoint:    Jungle Scout endpoint name (e.g. ``"product_database"``).
            marketplace: ISO marketplace code (e.g. ``"UK"``), or ``None``.
            pages:       Number of billable pages to reserve (default 1).
            launch_id:   Optional FK to ``launchpad.product_launches``.
            metadata:    Optional JSONB payload stored alongside the ledger row.

        Returns:
            ``True`` if the budget was available and the row was inserted.
            ``False`` if the budget is exhausted (no row inserted).
        """
        if not self.check_budget_available(conn, pages):
            remaining = self.get_remaining_calls(conn)
            logger.warning(
                "Budget exhausted for endpoint=%s pages=%d remaining=%d",
                endpoint,
                pages,
                remaining,
            )
            return False

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO launchpad.api_call_ledger
                    (script_name, endpoint, marketplace, billable_pages,
                     launch_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    script_name,
                    endpoint,
                    marketplace,
                    pages,
                    launch_id,
                    psycopg.types.json.Jsonb(metadata) if metadata else None,
                ),
            )
        conn.commit()
        logger.debug(
            "Budget reserved: endpoint=%s marketplace=%s pages=%d",
            endpoint,
            marketplace,
            pages,
        )
        return True

    # ------------------------------------------------------------------
    # Endpoint wrappers
    # ------------------------------------------------------------------

    def get_product_database(
        self,
        conn: psycopg.Connection,
        marketplace: str,
        script_name: str = "js_client",
        launch_id: int | None = None,
        **filters: Any,
    ) -> Any | None:
        """Query the Jungle Scout ``product_database`` endpoint.

        Reserves one billable page before making the API call.  Returns
        ``None`` (without calling the API) if the budget is exhausted.

        Args:
            conn:        Active psycopg connection for budget tracking.
            marketplace: ISO marketplace code (e.g. ``"UK"``, ``"DE"``).
            script_name: Caller identifier recorded in the ledger.
            launch_id:   Optional FK to ``launchpad.product_launches``.
            **filters:   Keyword arguments forwarded to the Jungle Scout client
                         (e.g. ``min_monthly_revenue=5000``).

        Returns:
            Jungle Scout API response, or ``None`` if budget is exhausted.

        Raises:
            BudgetExhaustedError: Never raised here; ``None`` is returned instead.
            Exception:            Re-raised on API / network errors.
        """
        endpoint = "product_database"
        if not self.reserve_budget(
            conn, script_name, endpoint, marketplace=marketplace, launch_id=launch_id
        ):
            return None

        try:
            return self._client.product_database(
                marketplace=marketplace, **filters
            )
        except Exception as exc:
            self._handle_api_error(exc, endpoint)
            raise

    def get_keywords_by_asin(
        self,
        conn: psycopg.Connection,
        asin: str,
        marketplace: str,
        script_name: str = "js_client",
        launch_id: int | None = None,
    ) -> Any | None:
        """Query the Jungle Scout ``keywords_by_asin`` endpoint.

        Args:
            conn:        Active psycopg connection for budget tracking.
            asin:        Amazon ASIN to look up keywords for.
            marketplace: ISO marketplace code.
            script_name: Caller identifier recorded in the ledger.
            launch_id:   Optional FK to ``launchpad.product_launches``.

        Returns:
            Jungle Scout API response, or ``None`` if budget is exhausted.
        """
        endpoint = "keywords_by_asin"
        if not self.reserve_budget(
            conn, script_name, endpoint, marketplace=marketplace, launch_id=launch_id
        ):
            return None

        try:
            return self._client.keywords_by_asin(
                asin=asin, marketplace=marketplace
            )
        except Exception as exc:
            self._handle_api_error(exc, endpoint)
            raise

    def get_sales_estimates(
        self,
        conn: psycopg.Connection,
        asin: str,
        marketplace: str,
        script_name: str = "js_client",
        launch_id: int | None = None,
    ) -> Any | None:
        """Query the Jungle Scout ``sales_estimates`` endpoint.

        Args:
            conn:        Active psycopg connection for budget tracking.
            asin:        Amazon ASIN to estimate sales for.
            marketplace: ISO marketplace code.
            script_name: Caller identifier recorded in the ledger.
            launch_id:   Optional FK to ``launchpad.product_launches``.

        Returns:
            Jungle Scout API response, or ``None`` if budget is exhausted.
        """
        endpoint = "sales_estimates"
        if not self.reserve_budget(
            conn, script_name, endpoint, marketplace=marketplace, launch_id=launch_id
        ):
            return None

        try:
            return self._client.sales_estimates(
                asin=asin, marketplace=marketplace
            )
        except Exception as exc:
            self._handle_api_error(exc, endpoint)
            raise

    def get_share_of_voice(
        self,
        conn: psycopg.Connection,
        keyword: str,
        marketplace: str,
        script_name: str = "js_client",
        launch_id: int | None = None,
    ) -> Any | None:
        """Query the Jungle Scout ``share_of_voice`` endpoint.

        Args:
            conn:        Active psycopg connection for budget tracking.
            keyword:     Search keyword to analyse.
            marketplace: ISO marketplace code.
            script_name: Caller identifier recorded in the ledger.
            launch_id:   Optional FK to ``launchpad.product_launches``.

        Returns:
            Jungle Scout API response, or ``None`` if budget is exhausted.
        """
        endpoint = "share_of_voice"
        if not self.reserve_budget(
            conn, script_name, endpoint, marketplace=marketplace, launch_id=launch_id
        ):
            return None

        try:
            return self._client.share_of_voice(
                keyword=keyword, marketplace=marketplace
            )
        except Exception as exc:
            self._handle_api_error(exc, endpoint)
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_api_error(self, exc: Exception, endpoint: str) -> None:
        """Log and classify API errors without swallowing them.

        Callers are expected to re-raise after calling this method.

        Args:
            exc:      The exception that was caught.
            endpoint: Endpoint name for log context.
        """
        exc_str = str(exc)
        # HTTP 429 — rate limit
        if "429" in exc_str or "rate limit" in exc_str.lower():
            logger.error(
                "Jungle Scout rate limit hit on endpoint=%s. "
                "Limit: 300 req/min, 15 req/sec. Back off and retry.",
                endpoint,
            )
        # Connection / network errors
        elif any(
            kw in exc_str.lower()
            for kw in ("connection", "timeout", "network", "ssl")
        ):
            logger.error(
                "Network error calling Jungle Scout endpoint=%s: %s",
                endpoint,
                exc,
            )
        else:
            logger.error(
                "Unexpected error from Jungle Scout endpoint=%s: %s",
                endpoint,
                exc,
            )
