"""Unit tests for economic opportunity forecasting."""

from services.opportunity_economics import (
    build_economic_estimate_from_snapshot,
    estimate_economic_opportunity,
)


def test_estimate_economic_opportunity_basic_shape():
    result = estimate_economic_opportunity(
        source_marketplace="US",
        target_marketplace="UK",
        target_market_value_monthly=100_000,
        source_share_assumption_pct=10.0,
        pursuit_score=72.0,
        competitor_count=14,
        moat_strength="Medium",
        recommended_price=24.99,
        competitor_price_median=23.99,
    )

    share = result["target_share_forecast_pct"]
    value = result["target_opportunity_value_monthly"]

    assert result["model"] == "bayesian_transfer_v1"
    assert share["p10"] <= share["expected"] <= share["p90"]
    assert value["p10"] <= value["expected"] <= value["p90"]
    assert value["parity_case"] == 10_000.0


def test_source_value_is_inferred_from_marketplace_ratio():
    result = estimate_economic_opportunity(
        source_marketplace="US",
        target_marketplace="UK",
        target_market_value_monthly=100_000,
        source_share_assumption_pct=10.0,
    )

    assumptions = result["assumptions"]
    source_value = assumptions["source_market_value_monthly"]

    # UK ratio is 0.10 vs US 1.0 -> US inferred value is 10x target value.
    assert source_value == 1_000_000.0
    assert result["source_opportunity_value_monthly"]["at_assumed_share"] == 100_000.0


def test_build_from_snapshot_uses_saved_fields():
    snapshot = {
        "launch": {
            "source_marketplace": "US",
            "pursuit_score": 68.0,
        },
        "marketplace": "UK",
        "latest_saved_pricing": {
            "recommended_launch_price": 19.99,
            "competitor_count": 12,
        },
        "competitor_analysis": {
            "competitor_price_p50": 18.99,
        },
        "review_moat_summary": {
            "moat_strength": "Weak",
        },
    }

    result = build_economic_estimate_from_snapshot(
        snapshot,
        target_market_value_monthly=80_000,
        source_share_assumption_pct=10.0,
    )

    assert result["assumptions"]["target_market_value_monthly"] == 80_000.0
    assert result["target_share_forecast_pct"]["parity_case"] == 10.0
