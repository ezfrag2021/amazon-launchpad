"""
Marketplace normalization and US→UK/EU mapping for Amazon Launchpad.

Stage 1 accepts US ASIN input and maps to UK/EU target markets.
This is a key difference from amazon-mi which excludes US entirely
(amazon-mi uses EXCLUDED_MARKETPLACES = ("US",)).
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical alias map: non-standard codes → canonical codes
MARKETPLACE_ALIASES: dict[str, str] = {
    "GB": "UK",
}

# Reverse alias map: canonical → all known variants
_REVERSE_ALIASES: dict[str, list[str]] = {}
for _alias, _canonical in MARKETPLACE_ALIASES.items():
    _REVERSE_ALIASES.setdefault(_canonical, [_canonical]).append(_alias)

# Default target marketplaces for a US-sourced launch (matches DB default)
# ARRAY['UK','DE','FR','IT','ES'] in 002_launchpad_core_tables.sql
DEFAULT_TARGET_MARKETPLACES: list[str] = ["UK", "DE", "FR", "IT", "ES"]

# Stage 1 only accepts US ASINs as the source marketplace
ALLOWED_INPUT_MARKETPLACE: str = "US"


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def normalize_marketplace_code(value: str) -> str:
    """Return the canonical uppercase marketplace code.

    Handles common aliases (e.g. GB → UK) and strips whitespace.

    Args:
        value: Raw marketplace code string (e.g. "gb", "GB", "UK").

    Returns:
        Canonical uppercase code (e.g. "UK").
    """
    if not isinstance(value, str):
        raise TypeError(f"Expected str, got {type(value).__name__!r}")
    upper = value.strip().upper()
    return MARKETPLACE_ALIASES.get(upper, upper)


def get_marketplace_variants(code: str) -> list[str]:
    """Return all known codes for a marketplace, including aliases.

    Example:
        get_marketplace_variants("UK") → ["UK", "GB"]
        get_marketplace_variants("DE") → ["DE"]

    Args:
        code: Marketplace code (will be normalised first).

    Returns:
        List of all equivalent codes, canonical first.
    """
    canonical = normalize_marketplace_code(code)
    return list(_REVERSE_ALIASES.get(canonical, [canonical]))


def filter_allowed_marketplaces(values: list[str]) -> list[str]:
    """Filter a list of marketplace codes to only the allowed target markets.

    Normalises each input code before checking membership.  US is never
    included in the target list.

    Args:
        values: Arbitrary list of marketplace code strings.

    Returns:
        Ordered list of normalised codes that appear in
        DEFAULT_TARGET_MARKETPLACES, preserving input order and
        de-duplicating.
    """
    allowed_set = set(DEFAULT_TARGET_MARKETPLACES)
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        try:
            normalised = normalize_marketplace_code(v)
        except TypeError:
            continue
        if normalised in allowed_set and normalised not in seen:
            result.append(normalised)
            seen.add(normalised)
    return result


# ---------------------------------------------------------------------------
# Source / target validation
# ---------------------------------------------------------------------------

def validate_source_marketplace(code: str) -> bool:
    """Return True only if *code* is the accepted Stage 1 source marketplace.

    Stage 1 exclusively accepts US ASINs.  Any other source marketplace
    (including UK/EU) is rejected here.

    Args:
        code: Marketplace code to validate (will be normalised).

    Returns:
        True if the code normalises to "US", False otherwise.
    """
    try:
        return normalize_marketplace_code(code) == ALLOWED_INPUT_MARKETPLACE
    except TypeError:
        return False


def get_target_marketplaces_for_launch(source: str) -> list[str]:
    """Return the default target marketplace list for a given source.

    Currently only US is supported as a source (Stage 1 constraint).
    Returns a fresh copy of DEFAULT_TARGET_MARKETPLACES so callers cannot
    mutate the module-level constant.

    Args:
        source: Source marketplace code (e.g. "US").

    Returns:
        List of target marketplace codes.

    Raises:
        ValueError: If *source* is not a supported source marketplace.
    """
    normalised = normalize_marketplace_code(source)
    if normalised != ALLOWED_INPUT_MARKETPLACE:
        raise ValueError(
            f"Unsupported source marketplace {normalised!r}. "
            f"Stage 1 only accepts {ALLOWED_INPUT_MARKETPLACE!r} as source."
        )
    return list(DEFAULT_TARGET_MARKETPLACES)
