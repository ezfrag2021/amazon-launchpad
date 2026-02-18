"""
Pricing Engine - Launch price predictor and PPC simulator for Stage 3 (Risk & Pricing Architect).

Analyses competitor pricing to recommend a launch price envelope, simulates PPC campaigns
per keyword, and calculates margin breakdowns. Results are persisted in
launchpad.pricing_analysis and launchpad.ppc_simulation tables.
"""

from __future__ import annotations

import statistics
from typing import Optional

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
DEFAULT_TARGET_MARGIN: float = 30.0
DEFAULT_TARGET_ACOS: float = 30.0
AMAZON_REFERRAL_FEE_PCT: float = 15.0

# CPC base rates by marketplace (GBP / EUR / USD equivalent)
_MARKETPLACE_CPC_BASE: dict[str, float] = {
    "UK": 0.45,
    "DE": 0.50,
    "FR": 0.42,
    "IT": 0.38,
    "ES": 0.35,
    "US": 0.65,
}

# Competition multipliers for CPC estimation
_COMPETITION_CPC_MULTIPLIER: dict[str, float] = {
    "low": 0.6,
    "medium": 1.0,
    "high": 1.8,
}

# Volume tiers for CPC adjustment (higher volume → more competition → higher CPC)
_VOLUME_CPC_TIERS: list[tuple[int, float]] = [
    (500, 0.7),
    (2_000, 0.9),
    (5_000, 1.0),
    (15_000, 1.2),
    (50_000, 1.5),
    (int(1e9), 1.8),  # 1B sentinel for "very high volume"
]

# Days-to-page-1 estimates by keyword difficulty bucket
_DAYS_TO_PAGE1_BY_DIFFICULTY: list[tuple[float, int]] = [
    (0.30, 14),   # CV ≤ 0.30 → stable market → ~14 days
    (0.50, 21),
    (0.70, 35),
    (1.00, 60),
    (float("inf"), 90),
]


# ---------------------------------------------------------------------------
# PricingEngine
# ---------------------------------------------------------------------------
class PricingEngine:
    """
    Launch price predictor and PPC simulator for Amazon UK/EU markets.

    All monetary values are in the marketplace's native currency.
    Percentages are expressed as plain floats (e.g. 30.0 means 30 %).
    """

    # ------------------------------------------------------------------
    # 1. Launch price envelope
    # ------------------------------------------------------------------

    def calculate_launch_price_envelope(
        self,
        competitor_prices: list[float],
        target_margin_pct: float = DEFAULT_TARGET_MARGIN,
        cost_of_goods: Optional[float] = None,
    ) -> dict:
        """
        Calculate a three-point launch price envelope from competitor data.

        Parameters
        ----------
        competitor_prices : list[float]
            Raw competitor prices in marketplace currency.
        target_margin_pct : float
            Minimum acceptable gross margin percentage (default 30 %).
        cost_of_goods : float | None
            Unit cost of goods (COGS). When provided, the price floor is
            raised to guarantee at least *target_margin_pct* margin after
            Amazon referral fees.

        Returns
        -------
        dict with keys:
            price_floor          – lowest viable price
            recommended_launch_price – suggested entry price
            price_ceiling        – upper bound (premium positioning)
            margin_estimate_pct  – estimated gross margin at recommended price
            competitor_price_p25 – 25th percentile of competitor prices
            competitor_price_p50 – median competitor price
            competitor_price_p75 – 75th percentile of competitor prices
            competitor_count     – number of competitors analysed
        """
        if not competitor_prices:
            raise ValueError("competitor_prices must not be empty")

        analysis = self.analyze_competitor_pricing(competitor_prices)
        p25 = analysis["competitor_price_p25"]
        p50 = analysis["competitor_price_p50"]
        p75 = analysis["competitor_price_p75"]

        # Default price points from competitor distribution
        price_floor = round(p25 * 0.95, 2)          # 5 % below p25
        recommended = round(p50 * 0.97, 2)           # slight undercut on median
        price_ceiling = round(p75 * 1.05, 2)         # 5 % above p75

        # If COGS provided, enforce margin floor
        if cost_of_goods is not None and cost_of_goods > 0:
            # Minimum price to achieve target_margin_pct after Amazon fees
            # net_margin = (price - cogs - amazon_fee) / price >= target_margin_pct / 100
            # price * (1 - amazon_fee_pct/100 - target_margin_pct/100) >= cogs
            # price >= cogs / (1 - (amazon_fee_pct + target_margin_pct) / 100)
            fee_and_margin = (AMAZON_REFERRAL_FEE_PCT + target_margin_pct) / 100.0
            if fee_and_margin >= 1.0:
                raise ValueError(
                    "Combined Amazon fee and target margin exceed 100 % — "
                    "no viable price exists."
                )
            margin_floor_price = round(cost_of_goods / (1.0 - fee_and_margin), 2)
            price_floor = max(price_floor, margin_floor_price)
            recommended = max(recommended, margin_floor_price)
            price_ceiling = max(price_ceiling, margin_floor_price)

        # Estimate margin at recommended price
        margin_estimate = self._estimate_margin_pct(recommended, cost_of_goods)

        return {
            "price_floor": price_floor,
            "recommended_launch_price": recommended,
            "price_ceiling": price_ceiling,
            "margin_estimate_pct": margin_estimate,
            "competitor_price_p25": p25,
            "competitor_price_p50": p50,
            "competitor_price_p75": p75,
            "competitor_count": analysis["competitor_count"],
        }

    # ------------------------------------------------------------------
    # 2. Competitor pricing analysis
    # ------------------------------------------------------------------

    def analyze_competitor_pricing(
        self,
        competitor_prices: list[float],
    ) -> dict:
        """
        Summarise competitor price distribution.

        Parameters
        ----------
        competitor_prices : list[float]
            Raw competitor prices.

        Returns
        -------
        dict with keys:
            competitor_price_p25  – 25th percentile
            competitor_price_p50  – median (50th percentile)
            competitor_price_p75  – 75th percentile
            competitor_count      – number of data points
            price_min             – minimum price
            price_max             – maximum price
            price_mean            – arithmetic mean
            coefficient_of_variation – std / mean (price stability indicator)
            price_stability       – 'stable' | 'moderate' | 'volatile'
        """
        if not competitor_prices:
            raise ValueError("competitor_prices must not be empty")

        prices = sorted(competitor_prices)
        n = len(prices)

        p25 = round(self._percentile(prices, 25), 2)
        p50 = round(self._percentile(prices, 50), 2)
        p75 = round(self._percentile(prices, 75), 2)
        price_min = round(min(prices), 2)
        price_max = round(max(prices), 2)
        price_mean = round(statistics.mean(prices), 2)

        if n >= 2 and price_mean > 0:
            cv = round(statistics.stdev(prices) / price_mean, 4)
        else:
            cv = 0.0

        if cv <= 0.15:
            stability = "stable"
        elif cv <= 0.35:
            stability = "moderate"
        else:
            stability = "volatile"

        return {
            "competitor_price_p25": p25,
            "competitor_price_p50": p50,
            "competitor_price_p75": p75,
            "competitor_count": n,
            "price_min": price_min,
            "price_max": price_max,
            "price_mean": price_mean,
            "coefficient_of_variation": cv,
            "price_stability": stability,
        }

    # ------------------------------------------------------------------
    # 3. PPC campaign simulation
    # ------------------------------------------------------------------

    def simulate_ppc_campaign(
        self,
        keywords: list[dict],
        daily_budget: float,
        target_acos: float = DEFAULT_TARGET_ACOS,
        marketplace: str = "UK",
    ) -> list[dict]:
        """
        Simulate a PPC campaign across a list of keywords.

        Parameters
        ----------
        keywords : list[dict]
            Each dict should contain at minimum:
                keyword          (str)  – keyword text
                search_volume    (int)  – monthly exact-match search volume
                competition_level (str) – 'low' | 'medium' | 'high'
            Optional fields:
                cpc              (float) – override estimated CPC
                source_field     (str)   – 'ppc_bid_exact' | 'ppc_bid_broad'
        daily_budget : float
            Total daily PPC budget in marketplace currency.
        target_acos : float
            Target Advertising Cost of Sale percentage (default 30 %).
        marketplace : str
            Marketplace code (UK, DE, FR, IT, ES, US).

        Returns
        -------
        list[dict]
            One dict per keyword, ready for insertion into ppc_simulation:
                keyword
                search_volume_exact
                estimated_cpc
                estimated_acos_pct
                estimated_tacos_pct
                organic_rank_target
                estimated_daily_spend
                estimated_days_to_page1
                source_field
        """
        if not keywords:
            return []

        results: list[dict] = []
        budget_remaining = daily_budget

        for kw in keywords:
            keyword_text = str(kw.get("keyword", ""))
            search_volume = int(kw.get("search_volume", 0))
            competition_level = str(kw.get("competition_level", "medium")).lower()
            source_field = str(kw.get("source_field", "ppc_bid_exact"))

            # CPC: use provided value or estimate
            if "cpc" in kw and kw["cpc"] is not None:
                estimated_cpc = round(float(kw["cpc"]), 2)
            else:
                estimated_cpc = self.estimate_cpc_from_keyword_data(
                    search_volume, competition_level, marketplace
                )

            # Daily spend for this keyword (capped by remaining budget)
            # Assume ~10 % of monthly search volume converts to daily impressions
            # and a 2 % CTR on sponsored ads
            daily_impressions = (search_volume / 30.0) * 0.10
            daily_clicks = daily_impressions * 0.02
            raw_daily_spend = round(daily_clicks * estimated_cpc, 2)
            estimated_daily_spend = round(min(raw_daily_spend, budget_remaining), 2)
            budget_remaining = max(0.0, budget_remaining - estimated_daily_spend)

            # ACoS estimate: CPC / (conversion_rate * price)
            # We don't have price here, so we express ACoS relative to target
            # Higher CPC relative to market base → higher ACoS
            base_cpc = _MARKETPLACE_CPC_BASE.get(marketplace.upper(), 0.45)
            cpc_ratio = estimated_cpc / base_cpc if base_cpc > 0 else 1.0
            estimated_acos_pct = round(
                min(target_acos * cpc_ratio * 1.1, 150.0), 2
            )

            # TACoS (Total ACoS) ≈ ACoS * (paid_sales / total_sales)
            # Assume paid sales are ~40 % of total at launch
            estimated_tacos_pct = round(estimated_acos_pct * 0.40, 2)

            # Organic rank target: page 1 = top 16 results
            organic_rank_target = 16

            # Days to page 1 based on competition level
            days_to_page1 = self._estimate_days_to_page1(competition_level)

            results.append(
                {
                    "keyword": keyword_text,
                    "search_volume_exact": search_volume,
                    "estimated_cpc": estimated_cpc,
                    "estimated_acos_pct": estimated_acos_pct,
                    "estimated_tacos_pct": estimated_tacos_pct,
                    "organic_rank_target": organic_rank_target,
                    "estimated_daily_spend": estimated_daily_spend,
                    "estimated_days_to_page1": days_to_page1,
                    "source_field": source_field,
                }
            )

        return results

    # ------------------------------------------------------------------
    # 4. CPC estimation
    # ------------------------------------------------------------------

    def estimate_cpc_from_keyword_data(
        self,
        search_volume: int,
        competition_level: str,
        marketplace: str,
    ) -> float:
        """
        Estimate CPC from keyword search volume and competition level.

        Parameters
        ----------
        search_volume : int
            Monthly exact-match search volume.
        competition_level : str
            'low' | 'medium' | 'high'
        marketplace : str
            Marketplace code (UK, DE, FR, IT, ES, US).

        Returns
        -------
        float
            Estimated CPC in marketplace currency, rounded to 2 dp.
        """
        base = _MARKETPLACE_CPC_BASE.get(marketplace.upper(), 0.45)
        competition_mult = _COMPETITION_CPC_MULTIPLIER.get(
            competition_level.lower(), 1.0
        )
        volume_mult = self._volume_cpc_multiplier(search_volume)
        cpc = round(base * competition_mult * volume_mult, 2)
        return max(0.01, cpc)

    # ------------------------------------------------------------------
    # 5. Margin calculation
    # ------------------------------------------------------------------

    def calculate_margin(
        self,
        price: float,
        cost_of_goods: float,
        amazon_fees_pct: float = AMAZON_REFERRAL_FEE_PCT,
        fulfillment_cost: float = 0.0,
    ) -> dict:
        """
        Calculate gross and net margin for a given price and cost structure.

        Parameters
        ----------
        price : float
            Selling price in marketplace currency.
        cost_of_goods : float
            Unit cost of goods (COGS).
        amazon_fees_pct : float
            Amazon referral fee percentage (default 15 %).
        fulfillment_cost : float
            FBA or FBM fulfilment cost per unit (default 0.0).

        Returns
        -------
        dict with keys:
            price
            cost_of_goods
            amazon_referral_fee   – absolute fee amount
            fulfillment_cost
            total_costs
            gross_profit
            gross_margin_pct
            net_profit
            net_margin_pct
            break_even_price      – minimum price to cover all costs
        """
        if price <= 0:
            raise ValueError("price must be positive")
        if cost_of_goods < 0:
            raise ValueError("cost_of_goods must be non-negative")

        amazon_referral_fee = round(price * amazon_fees_pct / 100.0, 2)
        total_costs = round(cost_of_goods + amazon_referral_fee + fulfillment_cost, 2)
        gross_profit = round(price - cost_of_goods - amazon_referral_fee, 2)
        net_profit = round(price - total_costs, 2)

        gross_margin_pct = round((gross_profit / price) * 100.0, 2) if price else 0.0
        net_margin_pct = round((net_profit / price) * 100.0, 2) if price else 0.0

        # Break-even: price where net_profit = 0
        # price = cogs + fulfillment + price * fee_pct/100
        # price * (1 - fee_pct/100) = cogs + fulfillment
        fee_fraction = amazon_fees_pct / 100.0
        if fee_fraction < 1.0:
            break_even_price = round(
                (cost_of_goods + fulfillment_cost) / (1.0 - fee_fraction), 2
            )
        else:
            break_even_price = float("inf")

        return {
            "price": round(price, 2),
            "cost_of_goods": round(cost_of_goods, 2),
            "amazon_referral_fee": amazon_referral_fee,
            "fulfillment_cost": round(fulfillment_cost, 2),
            "total_costs": total_costs,
            "gross_profit": gross_profit,
            "gross_margin_pct": gross_margin_pct,
            "net_profit": net_profit,
            "net_margin_pct": net_margin_pct,
            "break_even_price": break_even_price,
        }

    # ------------------------------------------------------------------
    # 6. Price viability assessment
    # ------------------------------------------------------------------

    def assess_price_viability(
        self,
        recommended_price: float,
        price_floor: float,
        price_ceiling: float,
        competitor_count: int,
    ) -> dict:
        """
        Assess whether a recommended price is viable in the competitive context.

        Parameters
        ----------
        recommended_price : float
            The proposed launch price.
        price_floor : float
            Minimum viable price (margin-constrained).
        price_ceiling : float
            Maximum competitive price.
        competitor_count : int
            Number of active competitors in the niche.

        Returns
        -------
        dict with keys:
            is_viable            – bool
            viability_score      – 0–100 float
            within_range         – bool (price_floor ≤ recommended ≤ price_ceiling)
            price_position       – 'below_floor' | 'competitive' | 'above_ceiling'
            competitor_count     – echoed back
            recommendations      – list[str]
        """
        within_range = price_floor <= recommended_price <= price_ceiling

        if recommended_price < price_floor:
            price_position = "below_floor"
        elif recommended_price > price_ceiling:
            price_position = "above_ceiling"
        else:
            price_position = "competitive"

        # Viability score: starts at 100, deductions for risk factors
        score = 100.0

        if not within_range:
            score -= 40.0

        # Penalise very crowded markets
        if competitor_count > 30:
            score -= 20.0
        elif competitor_count > 15:
            score -= 10.0

        # Penalise pricing above ceiling (premium risk)
        if price_position == "above_ceiling":
            overshoot_pct = (recommended_price - price_ceiling) / price_ceiling * 100
            score -= min(overshoot_pct, 30.0)

        # Penalise pricing below floor (margin risk)
        if price_position == "below_floor":
            undershoot_pct = (price_floor - recommended_price) / price_floor * 100
            score -= min(undershoot_pct, 30.0)

        viability_score = round(max(0.0, min(100.0, score)), 2)
        is_viable = viability_score >= 50.0 and within_range

        recommendations = self._build_viability_recommendations(
            price_position, viability_score, competitor_count,
            recommended_price, price_floor, price_ceiling,
        )

        return {
            "is_viable": is_viable,
            "viability_score": viability_score,
            "within_range": within_range,
            "price_position": price_position,
            "competitor_count": competitor_count,
            "recommendations": recommendations,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile(sorted_data: list[float], pct: float) -> float:
        """
        Calculate a percentile from a pre-sorted list using linear interpolation.
        """
        n = len(sorted_data)
        if n == 1:
            return sorted_data[0]
        index = (pct / 100.0) * (n - 1)
        lower = int(index)
        upper = min(lower + 1, n - 1)
        fraction = index - lower
        return sorted_data[lower] + fraction * (sorted_data[upper] - sorted_data[lower])

    @staticmethod
    def _volume_cpc_multiplier(search_volume: int) -> float:
        """Return a CPC multiplier based on monthly search volume tier."""
        for threshold, multiplier in _VOLUME_CPC_TIERS:
            if search_volume < threshold:
                return multiplier
        return _VOLUME_CPC_TIERS[-1][1]

    @staticmethod
    def _estimate_days_to_page1(competition_level: str) -> int:
        """
        Estimate days to reach page 1 organic ranking based on competition level.
        """
        mapping = {
            "low": 14,
            "medium": 30,
            "high": 60,
        }
        return mapping.get(competition_level.lower(), 30)

    def _estimate_margin_pct(
        self,
        price: float,
        cost_of_goods: Optional[float],
    ) -> Optional[float]:
        """
        Estimate gross margin percentage at a given price.
        Returns None if COGS is unknown.
        """
        if cost_of_goods is None or price <= 0:
            return None
        amazon_fee = price * AMAZON_REFERRAL_FEE_PCT / 100.0
        gross_profit = price - cost_of_goods - amazon_fee
        return round((gross_profit / price) * 100.0, 2)

    @staticmethod
    def _build_viability_recommendations(
        price_position: str,
        viability_score: float,
        competitor_count: int,
        recommended_price: float,
        price_floor: float,
        price_ceiling: float,
    ) -> list[str]:
        """Build actionable recommendations from viability assessment."""
        recs: list[str] = []

        if price_position == "below_floor":
            recs.append(
                f"Recommended price (£{recommended_price:.2f}) is below the margin "
                f"floor (£{price_floor:.2f}). Raise price or reduce COGS."
            )
        elif price_position == "above_ceiling":
            recs.append(
                f"Recommended price (£{recommended_price:.2f}) exceeds the competitive "
                f"ceiling (£{price_ceiling:.2f}). Justify premium with strong "
                "differentiation or lower the price."
            )
        else:
            recs.append(
                f"Price (£{recommended_price:.2f}) sits within the competitive range "
                f"(£{price_floor:.2f}–£{price_ceiling:.2f}). Good positioning."
            )

        if competitor_count > 30:
            recs.append(
                "High competitor count — consider aggressive launch pricing and "
                "PPC spend to gain initial velocity."
            )
        elif competitor_count > 15:
            recs.append(
                "Moderate competition — differentiate on listing quality and "
                "target long-tail keywords to reduce PPC costs."
            )
        else:
            recs.append(
                "Low competitor count — pricing flexibility is higher; "
                "consider testing at the upper end of the range."
            )

        if viability_score >= 80:
            recs.append("Strong price viability — proceed with confidence.")
        elif viability_score >= 50:
            recs.append(
                "Acceptable price viability — monitor margins closely post-launch."
            )
        else:
            recs.append(
                "Low viability score — revisit pricing strategy before launch."
            )

        return recs
