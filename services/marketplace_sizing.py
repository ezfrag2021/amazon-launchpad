"""
Rough market size ratios for cross-market volume estimation.

These are simplified constants for quick prospect assessment — NOT precise
forecasts.  They represent approximate relative market sizes compared to the
US Amazon marketplace (baseline = 1.0).

Typical use-case: a product sells X units/month in UK; what might it sell in
the US (source market), or in DE/FR/IT/ES (other EU targets)?

Sources / rationale:
  - EU5 combined is roughly 40% of the US Amazon market by GMV.
  - UK alone is ~10%, Germany ~12% (largest EU market), France ~8%,
    Italy ~6%, Spain ~4%.
  - These figures are order-of-magnitude estimates only.
"""

from services.marketplace_policy import normalize_marketplace_code

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Relative market size vs US (US = 1.0 baseline).
# Keys are canonical marketplace codes as returned by normalize_marketplace_code.
MARKET_SIZE_RATIOS: dict[str, float] = {
    "US": 1.00,
    "UK": 0.10,
    "DE": 0.12,
    "FR": 0.08,
    "IT": 0.06,
    "ES": 0.04,
}

# Default fallback ratio for unknown marketplaces (conservative estimate).
_DEFAULT_RATIO: float = 0.05


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_market_size_ratio(marketplace: str) -> float:
    """Return the relative market size ratio for *marketplace* vs US.

    US is the baseline (1.0).  All other values are fractions of the US
    market size.  Unknown marketplaces return a conservative default of
    ``_DEFAULT_RATIO`` (0.05).

    These are rough estimates for quick comparison only — not precise
    forecasts.

    Args:
        marketplace: Marketplace code (e.g. "UK", "GB", "DE").  Will be
            normalised via :func:`normalize_marketplace_code`.

    Returns:
        Float ratio in the range (0, 1].  US returns 1.0.
    """
    code = normalize_marketplace_code(marketplace)
    return MARKET_SIZE_RATIOS.get(code, _DEFAULT_RATIO)


def convert_volume(
    source_units: float,
    source_market: str,
    target_market: str,
) -> float:
    """Estimate sales volume in *target_market* given *source_units* in *source_market*.

    Uses the ratio of market sizes to scale the volume linearly.  This is a
    rough order-of-magnitude estimate only — actual performance depends on
    many factors (competition, pricing, localisation, etc.).

    Example::

        # If a product sells 1 000 units/month in UK, estimate US volume:
        convert_volume(1000, "UK", "US")  # → ~10 000

        # If a product sells 10 000 units/month in US, estimate DE volume:
        convert_volume(10_000, "US", "DE")  # → ~1 200

    Args:
        source_units: Observed monthly unit sales in the source market.
        source_market: Marketplace code for the known data point.
        target_market: Marketplace code to estimate volume for.

    Returns:
        Estimated unit sales in the target market (float).

    Raises:
        ValueError: If *source_units* is negative.
    """
    if source_units < 0:
        raise ValueError(f"source_units must be non-negative, got {source_units!r}")

    source_ratio = get_market_size_ratio(source_market)
    target_ratio = get_market_size_ratio(target_market)

    # Avoid division by zero (should never happen with valid ratios > 0)
    if source_ratio == 0:
        return 0.0

    return source_units * (target_ratio / source_ratio)


def estimate_us_volume_from_uk(uk_units: float) -> float:
    """Estimate US monthly unit sales from observed UK unit sales.

    Convenience wrapper around :func:`convert_volume` for the most common
    reverse-calculation: we have UK (GB) market_intel data and want to
    estimate what the product might sell in the US source market.

    Assumes UK ≈ 10% of US market (ratio 0.10), so US ≈ 10× UK.

    These are rough estimates for quick prospect assessment only.

    Args:
        uk_units: Observed monthly unit sales on Amazon UK.

    Returns:
        Estimated monthly unit sales on Amazon US.

    Raises:
        ValueError: If *uk_units* is negative.
    """
    return convert_volume(uk_units, source_market="UK", target_market="US")
