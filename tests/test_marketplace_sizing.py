"""Unit tests for marketplace_sizing module."""

import pytest

from services.marketplace_sizing import (
    MARKET_SIZE_RATIOS,
    convert_volume,
    estimate_us_volume_from_uk,
    get_market_size_ratio,
)


class TestMarketSizeRatios:
    """Test the market size ratio constants and lookups."""

    def test_us_is_baseline(self):
        """US should be 1.0 (100% baseline)."""
        assert MARKET_SIZE_RATIOS["US"] == 1.0
        assert get_market_size_ratio("US") == 1.0

    def test_uk_is_ten_percent(self):
        """UK should be ~10% of US market."""
        assert MARKET_SIZE_RATIOS["UK"] == 0.10
        assert get_market_size_ratio("UK") == 0.10

    def test_germany_is_largest_eu(self):
        """Germany should be largest EU market at ~12%."""
        assert MARKET_SIZE_RATIOS["DE"] == 0.12
        assert get_market_size_ratio("DE") == 0.12

    def test_eu5_total(self):
        """EU5 markets should total ~40% of US."""
        eu5_total = (
            MARKET_SIZE_RATIOS["UK"]
            + MARKET_SIZE_RATIOS["DE"]
            + MARKET_SIZE_RATIOS["FR"]
            + MARKET_SIZE_RATIOS["IT"]
            + MARKET_SIZE_RATIOS["ES"]
        )
        assert eu5_total == pytest.approx(0.40, abs=0.01)

    def test_gb_alias_normalizes_to_uk(self):
        """GB should normalize to UK ratio."""
        assert get_market_size_ratio("GB") == 0.10
        assert get_market_size_ratio("gb") == 0.10
        assert get_market_size_ratio("Uk") == 0.10

    def test_unknown_market_uses_default(self):
        """Unknown marketplaces should return conservative default (5%)."""
        assert get_market_size_ratio("XX") == 0.05
        assert get_market_size_ratio("UNKNOWN") == 0.05


class TestVolumeConversion:
    """Test cross-market volume estimation."""

    def test_uk_to_us_conversion(self):
        """UK volume should scale up 10x for US estimate."""
        uk_units = 1000
        us_estimate = convert_volume(uk_units, "UK", "US")
        # UK is 10% of US, so US = UK / 0.10 = UK * 10
        assert us_estimate == 10_000

    def test_us_to_uk_conversion(self):
        """US volume should scale down 10x for UK estimate."""
        us_units = 10_000
        uk_estimate = convert_volume(us_units, "US", "UK")
        assert uk_estimate == 1_000

    def test_us_to_germany_conversion(self):
        """US to Germany should be ~12% of US volume."""
        us_units = 10_000
        de_estimate = convert_volume(us_units, "US", "DE")
        assert de_estimate == 1_200

    def test_eu5_estimates_from_us(self):
        """All EU5 estimates from US baseline should sum to ~40%."""
        us_units = 10_000
        eu5_estimates = {
            "UK": convert_volume(us_units, "US", "UK"),
            "DE": convert_volume(us_units, "US", "DE"),
            "FR": convert_volume(us_units, "US", "FR"),
            "IT": convert_volume(us_units, "US", "IT"),
            "ES": convert_volume(us_units, "US", "ES"),
        }
        total_eu5 = sum(eu5_estimates.values())
        # Should be ~40% of US volume
        assert total_eu5 == pytest.approx(4_000, abs=100)

    def test_estimate_us_from_uk_wrapper(self):
        """Convenience wrapper should correctly reverse-calculate."""
        uk_units = 500
        us_estimate = estimate_us_volume_from_uk(uk_units)
        assert us_estimate == 5_000

    def test_same_market_no_change(self):
        """Converting within same market should return same value."""
        us_units = 10_000
        us_estimate = convert_volume(us_units, "US", "US")
        assert us_estimate == us_units

    def test_zero_units_returns_zero(self):
        """Zero units should return zero regardless of markets."""
        assert convert_volume(0, "UK", "US") == 0
        assert convert_volume(0, "US", "DE") == 0

    def test_negative_units_raises_error(self):
        """Negative units should raise ValueError."""
        with pytest.raises(ValueError, match="must be non-negative"):
            convert_volume(-100, "UK", "US")

        with pytest.raises(ValueError, match="must be non-negative"):
            estimate_us_volume_from_uk(-500)


class TestRealisticScenarios:
    """Test realistic business scenarios."""

    def test_moderate_uk_product(self):
        """A product selling 2,000 units/month in UK."""
        uk_monthly = 2_000
        us_estimate = estimate_us_volume_from_uk(uk_monthly)

        # Should estimate ~20,000 in US
        assert us_estimate == 20_000

        # Target projections from US estimate
        targets = {
            "UK": convert_volume(us_estimate, "US", "UK"),
            "DE": convert_volume(us_estimate, "US", "DE"),
            "FR": convert_volume(us_estimate, "US", "FR"),
            "IT": convert_volume(us_estimate, "US", "IT"),
            "ES": convert_volume(us_estimate, "US", "ES"),
        }

        assert targets["UK"] == 2_000  # Back to original
        assert targets["DE"] == 2_400  # 12% of 20,000
        assert targets["FR"] == 1_600  # 8% of 20,000
        assert targets["IT"] == 1_200  # 6% of 20,000
        assert targets["ES"] == 800    # 4% of 20,000

    def test_high_volume_uk_product(self):
        """A high-volume product selling 10,000 units/month in UK."""
        uk_monthly = 10_000
        us_estimate = estimate_us_volume_from_uk(uk_monthly)

        # Should estimate ~100,000 in US
        assert us_estimate == 100_000

        # EU5 total should be ~40,000
        eu5_total = sum(
            convert_volume(us_estimate, "US", mkt)
            for mkt in ["UK", "DE", "FR", "IT", "ES"]
        )
        assert eu5_total == 40_000

    def test_small_niche_product(self):
        """A small niche product selling 200 units/month in UK."""
        uk_monthly = 200
        us_estimate = estimate_us_volume_from_uk(uk_monthly)

        # Should estimate ~2,000 in US
        assert us_estimate == 2_000

        # Projections
        assert convert_volume(us_estimate, "US", "UK") == 200
        assert convert_volume(us_estimate, "US", "DE") == 240
        assert convert_volume(us_estimate, "US", "FR") == 160
