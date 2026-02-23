"""Economic opportunity forecasting helpers for opportunity reports.

The estimator is intentionally lightweight and API-free. It uses available
launch signals (pursuit score, moat, competitor density, price fit) to produce
an uncertainty-aware transfer model from source-market share to target-market
share.
"""

from __future__ import annotations

import math
from typing import Any

from services.marketplace_policy import normalize_marketplace_code
from services.marketplace_sizing import get_market_size_ratio


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _beta_p10_p90(alpha: float, beta: float) -> tuple[float, float]:
    """Approximate Beta P10/P90 using normal approximation.

    Good enough for report-level uncertainty bands without introducing scipy.
    """
    total = alpha + beta
    if total <= 0:
        return 0.0, 0.0

    mean = alpha / total
    var = (alpha * beta) / ((total * total) * (total + 1.0))
    std = math.sqrt(max(0.0, var))

    # z-score for 10th/90th percentile of standard normal.
    z = 1.2815515655446004
    p10 = _clamp(mean - z * std, 0.0, 1.0)
    p90 = _clamp(mean + z * std, 0.0, 1.0)
    return p10, p90


def _transfer_signal(
    *,
    source_marketplace: str,
    target_marketplace: str,
    pursuit_score: float | None,
    competitor_count: int | None,
    moat_strength: str | None,
    recommended_price: float | None,
    competitor_price_median: float | None,
) -> tuple[float, int, dict[str, float]]:
    """Build transferability signal in [0, 1] and a signal-count strength."""
    signal = 0.55
    used = 0
    components: dict[str, float] = {}

    source = normalize_marketplace_code(source_marketplace)
    target = normalize_marketplace_code(target_marketplace)

    market_delta = 0.08 if source == target else -0.05
    signal += market_delta
    components["market_match"] = market_delta

    if pursuit_score is not None:
        p = _clamp(pursuit_score, 0.0, 100.0)
        delta = ((p / 100.0) - 0.5) * 0.30
        signal += delta
        components["pursuit_score"] = delta
        used += 1

    if competitor_count is not None:
        c = max(0, competitor_count)
        if c <= 8:
            delta = 0.08
        elif c <= 20:
            delta = 0.03
        elif c <= 40:
            delta = -0.03
        else:
            delta = -0.08
        signal += delta
        components["competitor_density"] = delta
        used += 1

    if moat_strength:
        moat = str(moat_strength).strip().lower()
        if moat == "weak":
            delta = 0.08
        elif moat == "medium":
            delta = 0.00
        elif moat == "strong":
            delta = -0.10
        else:
            delta = 0.00
        signal += delta
        components["review_moat"] = delta
        used += 1

    if (
        recommended_price is not None
        and competitor_price_median is not None
        and competitor_price_median > 0
    ):
        rel_gap = (
            abs(recommended_price - competitor_price_median) / competitor_price_median
        )
        if rel_gap <= 0.10:
            delta = 0.04
        elif rel_gap <= 0.25:
            delta = 0.00
        else:
            delta = -0.06
        signal += delta
        components["price_fit"] = delta
        used += 1

    return _clamp(signal, 0.10, 0.95), used, components


def estimate_economic_opportunity(
    *,
    source_marketplace: str,
    target_marketplace: str,
    target_market_value_monthly: float,
    source_market_value_monthly: float | None = None,
    source_share_assumption_pct: float = 10.0,
    pursuit_score: float | None = None,
    competitor_count: int | None = None,
    moat_strength: str | None = None,
    recommended_price: float | None = None,
    competitor_price_median: float | None = None,
) -> dict[str, Any]:
    """Estimate transfer-adjusted market-share opportunity.

    Returns expected share, P10/P90 share band, and source/target monthly value
    opportunity using an API-free Bayesian transferability model.
    """
    target_value = max(0.0, float(target_market_value_monthly or 0.0))
    source_share = _clamp(float(source_share_assumption_pct or 0.0) / 100.0, 0.0, 1.0)

    inferred_source_value = None
    if source_market_value_monthly is not None:
        inferred_source_value = max(0.0, float(source_market_value_monthly))
    elif target_value > 0:
        src_ratio = get_market_size_ratio(source_marketplace)
        tgt_ratio = get_market_size_ratio(target_marketplace)
        if tgt_ratio > 0:
            inferred_source_value = target_value * (src_ratio / tgt_ratio)

    signal, signal_count, components = _transfer_signal(
        source_marketplace=source_marketplace,
        target_marketplace=target_marketplace,
        pursuit_score=pursuit_score,
        competitor_count=competitor_count,
        moat_strength=moat_strength,
        recommended_price=recommended_price,
        competitor_price_median=competitor_price_median,
    )

    # Prior: transferability centered at 0.60 with moderate confidence.
    prior_strength = 12.0
    prior_mean = 0.60
    alpha0 = prior_mean * prior_strength
    beta0 = (1.0 - prior_mean) * prior_strength

    evidence_strength = 10.0 + (signal_count * 3.0)
    alpha = alpha0 + (signal * evidence_strength)
    beta = beta0 + ((1.0 - signal) * evidence_strength)

    transfer_mean = alpha / (alpha + beta)
    transfer_p10, transfer_p90 = _beta_p10_p90(alpha, beta)

    target_share_expected = source_share * transfer_mean
    target_share_p10 = source_share * transfer_p10
    target_share_p90 = source_share * transfer_p90
    parity_target_share = source_share

    return {
        "model": "bayesian_transfer_v1",
        "assumptions": {
            "source_share_assumption_pct": round(source_share * 100.0, 2),
            "source_marketplace": normalize_marketplace_code(source_marketplace),
            "target_marketplace": normalize_marketplace_code(target_marketplace),
            "target_market_value_monthly": round(target_value, 2),
            "source_market_value_monthly": (
                round(inferred_source_value, 2)
                if inferred_source_value is not None
                else None
            ),
            "api_calls_used": 0,
        },
        "transferability": {
            "signal": round(signal, 4),
            "signal_components": {k: round(v, 4) for k, v in components.items()},
            "posterior_mean": round(transfer_mean, 4),
            "posterior_p10": round(transfer_p10, 4),
            "posterior_p90": round(transfer_p90, 4),
        },
        "target_share_forecast_pct": {
            "expected": round(target_share_expected * 100.0, 2),
            "p10": round(target_share_p10 * 100.0, 2),
            "p90": round(target_share_p90 * 100.0, 2),
            "parity_case": round(parity_target_share * 100.0, 2),
        },
        "target_opportunity_value_monthly": {
            "expected": round(target_value * target_share_expected, 2),
            "p10": round(target_value * target_share_p10, 2),
            "p90": round(target_value * target_share_p90, 2),
            "parity_case": round(target_value * parity_target_share, 2),
        },
        "source_opportunity_value_monthly": {
            "at_assumed_share": (
                round(inferred_source_value * source_share, 2)
                if inferred_source_value is not None
                else None
            )
        },
    }


def build_economic_estimate_from_snapshot(
    snapshot: dict[str, Any],
    *,
    target_market_value_monthly: float,
    source_share_assumption_pct: float = 10.0,
    source_market_value_monthly: float | None = None,
) -> dict[str, Any]:
    """Convenience wrapper that reads values from report snapshot."""
    launch = snapshot.get("launch", {}) if isinstance(snapshot, dict) else {}
    pricing = (
        snapshot.get("latest_saved_pricing", {}) if isinstance(snapshot, dict) else {}
    )
    moat = snapshot.get("review_moat_summary", {}) if isinstance(snapshot, dict) else {}

    pursuit_score = _safe_float(launch.get("pursuit_score"))
    competitor_count = launch.get("competitor_count")
    if competitor_count is None:
        competitor_count = pricing.get("competitor_count")
    try:
        competitor_count_int = (
            int(competitor_count) if competitor_count is not None else None
        )
    except (TypeError, ValueError):
        competitor_count_int = None

    return estimate_economic_opportunity(
        source_marketplace=str(launch.get("source_marketplace") or "US"),
        target_marketplace=str(snapshot.get("marketplace") or "UK"),
        target_market_value_monthly=target_market_value_monthly,
        source_market_value_monthly=source_market_value_monthly,
        source_share_assumption_pct=source_share_assumption_pct,
        pursuit_score=pursuit_score,
        competitor_count=competitor_count_int,
        moat_strength=str(moat.get("moat_strength") or ""),
        recommended_price=_safe_float(pricing.get("recommended_launch_price")),
        competitor_price_median=_safe_float(
            (snapshot.get("competitor_analysis") or {}).get("competitor_price_p50")
        ),
    )
