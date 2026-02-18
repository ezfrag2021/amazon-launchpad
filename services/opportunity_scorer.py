"""
Opportunity Scorer - Pursuit Score engine for Stage 1 (Opportunity Validator).

Takes competitor data (from Jungle Scout API) and produces a Pursuit Score
that determines if a product opportunity is worth pursuing. The score and
category are persisted in product_launches.pursuit_score / pursuit_category.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Thresholds & category labels (MUST NOT be changed)
# ---------------------------------------------------------------------------
SATURATED_THRESHOLD: float = 40.0
PROVEN_THRESHOLD: float = 70.0

CATEGORY_SATURATED: str = "Saturated"
CATEGORY_PROVEN: str = "Proven"
CATEGORY_GOLDMINE: str = "Goldmine"

# ---------------------------------------------------------------------------
# Scoring weights (sum = 1.0)
# ---------------------------------------------------------------------------
_WEIGHTS: dict[str, float] = {
    "competitor_density": 0.20,
    "review_moat": 0.25,
    "market_stability": 0.15,
    "rating_gap": 0.10,
    "sales_velocity": 0.20,
    "keyword_difficulty": 0.10,
}

# ---------------------------------------------------------------------------
# Helper sub-score reference ranges
# ---------------------------------------------------------------------------
# Competitor density: 0 competitors → 100 pts, 50+ → 0 pts
_COMPETITOR_SATURATION_LIMIT: int = 50

# Review moat: 0 avg reviews → 100 pts, 5000+ → 0 pts
_REVIEW_MOAT_SATURATION_LIMIT: float = 5_000.0

# Market stability: 0 new reviews/30d → 100 pts, 500+ → 0 pts
_VELOCITY_SATURATION_LIMIT: float = 500.0

# Rating gap: avg rating 1.0 → 100 pts (huge gap), 5.0 → 0 pts
_RATING_BEST: float = 5.0
_RATING_WORST: float = 1.0


# ---------------------------------------------------------------------------
# Dataclass for structured score breakdown (optional, for debugging/logging)
# ---------------------------------------------------------------------------
@dataclass
class ScoreBreakdown:
    competitor_density_score: float
    review_moat_score: float
    market_stability_score: float
    rating_gap_score: float
    sales_velocity_score: float
    keyword_difficulty_score: float
    weighted_score: float
    adjusted_score: float
    category: str


# ---------------------------------------------------------------------------
# OpportunityScorer
# ---------------------------------------------------------------------------
class OpportunityScorer:
    """
    Calculates a Pursuit Score (0–100) for an Amazon product opportunity.

    Score thresholds:
        < 40  → Saturated  (market too crowded / hard to enter)
        40–70 → Proven     (validated demand, moderate competition)
        > 70  → Goldmine   (strong opportunity, low barriers)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_pursuit_score(
        self,
        competitor_count: int,
        avg_review_count: float,
        review_velocity_30d: float,
        avg_rating: float,
        sales_velocity_score: float,   # normalised 0–100
        keyword_difficulty: float,     # normalised 0–100
        price_stability: float = 1.0,  # 0–1 multiplier
    ) -> tuple[float, str]:
        """
        Calculate the Pursuit Score for a product opportunity.

        Parameters
        ----------
        competitor_count : int
            Number of competing products in the niche.
        avg_review_count : float
            Average review count across top competitors.
        review_velocity_30d : float
            New reviews per 30 days across top 10 competitors.
        avg_rating : float
            Average star rating of top competitors (1.0–5.0).
        sales_velocity_score : float
            Normalised sales velocity score (0–100, higher = more sales).
        keyword_difficulty : float
            Normalised keyword difficulty (0–100, higher = harder to rank).
        price_stability : float
            Price stability multiplier (0–1). Default 1.0 (fully stable).

        Returns
        -------
        tuple[float, str]
            (pursuit_score, category) where category is one of
            'Saturated', 'Proven', or 'Goldmine'.
        """
        # Clamp inputs to valid ranges
        price_stability = max(0.0, min(1.0, price_stability))
        sales_velocity_score = max(0.0, min(100.0, sales_velocity_score))
        keyword_difficulty = max(0.0, min(100.0, keyword_difficulty))

        # Individual sub-scores (all 0–100)
        cd_score = self.analyze_competitor_density(competitor_count)
        rm_score = self.analyze_review_moat(avg_review_count)
        ms_score = self.analyze_market_stability(review_velocity_30d)
        rg_score = self._analyze_rating_gap(avg_rating)
        sv_score = sales_velocity_score                          # already normalised
        kd_score = 100.0 - keyword_difficulty                   # invert: lower difficulty = higher score

        # Weighted sum
        weighted = (
            cd_score * _WEIGHTS["competitor_density"]
            + rm_score * _WEIGHTS["review_moat"]
            + ms_score * _WEIGHTS["market_stability"]
            + rg_score * _WEIGHTS["rating_gap"]
            + sv_score * _WEIGHTS["sales_velocity"]
            + kd_score * _WEIGHTS["keyword_difficulty"]
        )

        # Apply price stability multiplier (unstable prices reduce attractiveness)
        adjusted = weighted * price_stability

        # Round to 2 decimal places (matches NUMERIC(5,2) in DB)
        score = round(max(0.0, min(100.0, adjusted)), 2)
        category = self.categorize_score(score)

        return score, category

    def get_score_breakdown(
        self,
        competitor_count: int,
        avg_review_count: float,
        review_velocity_30d: float,
        avg_rating: float,
        sales_velocity_score: float,
        keyword_difficulty: float,
        price_stability: float = 1.0,
    ) -> ScoreBreakdown:
        """
        Return a full breakdown of sub-scores for debugging / logging.
        """
        price_stability = max(0.0, min(1.0, price_stability))
        sales_velocity_score = max(0.0, min(100.0, sales_velocity_score))
        keyword_difficulty = max(0.0, min(100.0, keyword_difficulty))

        cd_score = self.analyze_competitor_density(competitor_count)
        rm_score = self.analyze_review_moat(avg_review_count)
        ms_score = self.analyze_market_stability(review_velocity_30d)
        rg_score = self._analyze_rating_gap(avg_rating)
        sv_score = sales_velocity_score
        kd_score = 100.0 - keyword_difficulty

        weighted = (
            cd_score * _WEIGHTS["competitor_density"]
            + rm_score * _WEIGHTS["review_moat"]
            + ms_score * _WEIGHTS["market_stability"]
            + rg_score * _WEIGHTS["rating_gap"]
            + sv_score * _WEIGHTS["sales_velocity"]
            + kd_score * _WEIGHTS["keyword_difficulty"]
        )
        adjusted = round(max(0.0, min(100.0, weighted * price_stability)), 2)
        category = self.categorize_score(adjusted)

        return ScoreBreakdown(
            competitor_density_score=round(cd_score, 2),
            review_moat_score=round(rm_score, 2),
            market_stability_score=round(ms_score, 2),
            rating_gap_score=round(rg_score, 2),
            sales_velocity_score=round(sv_score, 2),
            keyword_difficulty_score=round(kd_score, 2),
            weighted_score=round(weighted, 2),
            adjusted_score=adjusted,
            category=category,
        )

    # ------------------------------------------------------------------
    # Competitor analysis helpers
    # ------------------------------------------------------------------

    def analyze_competitor_density(self, competitor_count: int) -> float:
        """
        Score competitor density (0–100).

        Fewer competitors → higher score (easier to enter market).
        0 competitors → 100, _COMPETITOR_SATURATION_LIMIT+ → 0.
        Uses a linear decay capped at the saturation limit.
        """
        if competitor_count <= 0:
            return 100.0
        if competitor_count >= _COMPETITOR_SATURATION_LIMIT:
            return 0.0
        return round(
            100.0 * (1.0 - competitor_count / _COMPETITOR_SATURATION_LIMIT), 2
        )

    def analyze_review_moat(self, avg_review_count: float) -> float:
        """
        Score review moat strength as an *opportunity* metric (0–100).

        Lower average review count → higher score (easier to compete).
        0 reviews → 100, _REVIEW_MOAT_SATURATION_LIMIT+ → 0.
        Uses a logarithmic decay so the score drops quickly at low counts
        (where the moat builds fastest) and flattens at high counts.
        """
        if avg_review_count <= 0:
            return 100.0
        if avg_review_count >= _REVIEW_MOAT_SATURATION_LIMIT:
            return 0.0

        # log-based decay: score = 100 * (1 - log(x+1) / log(limit+1))
        score = 100.0 * (
            1.0
            - math.log(avg_review_count + 1)
            / math.log(_REVIEW_MOAT_SATURATION_LIMIT + 1)
        )
        return round(max(0.0, min(100.0, score)), 2)

    def analyze_market_stability(self, review_velocity_30d: float) -> float:
        """
        Score market stability (0–100).

        Lower review velocity → higher score (stable, not hyper-competitive).
        0 new reviews/30d → 100, _VELOCITY_SATURATION_LIMIT+ → 0.
        Uses linear decay.
        """
        if review_velocity_30d <= 0:
            return 100.0
        if review_velocity_30d >= _VELOCITY_SATURATION_LIMIT:
            return 0.0
        return round(
            100.0 * (1.0 - review_velocity_30d / _VELOCITY_SATURATION_LIMIT), 2
        )

    # ------------------------------------------------------------------
    # Score interpretation
    # ------------------------------------------------------------------

    def categorize_score(self, score: float) -> str:
        """
        Map a numeric Pursuit Score to a category label.

        < 40  → 'Saturated'
        40–70 → 'Proven'
        > 70  → 'Goldmine'
        """
        if score < SATURATED_THRESHOLD:
            return CATEGORY_SATURATED
        if score <= PROVEN_THRESHOLD:
            return CATEGORY_PROVEN
        return CATEGORY_GOLDMINE

    def get_score_recommendations(self, score: float, category: str) -> list[str]:
        """
        Return actionable recommendations based on the Pursuit Score and category.

        Parameters
        ----------
        score : float
            The calculated Pursuit Score (0–100).
        category : str
            One of 'Saturated', 'Proven', or 'Goldmine'.

        Returns
        -------
        list[str]
            Ordered list of recommendation strings.
        """
        recommendations: list[str] = []

        if category == CATEGORY_GOLDMINE:
            recommendations.append(
                "Strong opportunity detected — proceed to Stage 2 (Compliance Compass)."
            )
            recommendations.append(
                "Prioritise fast market entry to capitalise on low competition."
            )
            if score >= 85:
                recommendations.append(
                    "Exceptional score: consider allocating premium PPC budget at launch."
                )
            recommendations.append(
                "Validate demand with a small test order before full inventory commitment."
            )

        elif category == CATEGORY_PROVEN:
            recommendations.append(
                "Validated market with moderate competition — differentiation is key."
            )
            recommendations.append(
                "Analyse top-3 competitor listings for feature gaps and review complaints."
            )
            if score >= 60:
                recommendations.append(
                    "Score is in the upper Proven range — a strong product can still win."
                )
            else:
                recommendations.append(
                    "Score is in the lower Proven range — ensure clear USP before proceeding."
                )
            recommendations.append(
                "Consider targeting long-tail keywords to reduce initial PPC costs."
            )

        else:  # CATEGORY_SATURATED
            recommendations.append(
                "Market appears saturated — high risk of poor ROI without strong differentiation."
            )
            recommendations.append(
                "Review moat is likely high; budget for aggressive review acquisition strategy."
            )
            recommendations.append(
                "Consider pivoting to a sub-niche or adjacent product with lower competition."
            )
            if score >= 30:
                recommendations.append(
                    "Score is borderline — re-evaluate with updated competitor data before abandoning."
                )
            else:
                recommendations.append(
                    "Low score suggests this niche is not viable without a significant product innovation."
                )

        return recommendations

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _analyze_rating_gap(self, avg_rating: float) -> float:
        """
        Score the rating gap opportunity (0–100).

        Lower average competitor rating → higher score (room for a better product).
        avg_rating 1.0 → 100, avg_rating 5.0 → 0.
        """
        avg_rating = max(_RATING_WORST, min(_RATING_BEST, avg_rating))
        score = 100.0 * (_RATING_BEST - avg_rating) / (_RATING_BEST - _RATING_WORST)
        return round(score, 2)
