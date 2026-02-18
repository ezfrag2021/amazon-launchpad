"""
Services package for amazon-launch.

Provides database connectivity, authentication, marketplace policy,
and JungleScout API client as a unified interface.
"""

from services.db_connection import (
    DEFAULT_ROLE,
    resolve_dsn,
    normalize_dsn,
    inject_role,
    connect,
)

from services.auth_manager import (
    resolve_service_account_key_path,
    get_generative_client,
)

from services.marketplace_policy import (
    MARKETPLACE_ALIASES,
    DEFAULT_TARGET_MARKETPLACES,
    normalize_marketplace_code,
    get_marketplace_variants,
    filter_allowed_marketplaces,
    validate_source_marketplace,
)

from services.js_client import (
    JungleScoutClient,
    BudgetExhaustedError,
)

__all__ = [
    # db_connection
    "DEFAULT_ROLE",
    "resolve_dsn",
    "normalize_dsn",
    "inject_role",
    "connect",
    # auth_manager
    "resolve_service_account_key_path",
    "get_generative_client",
    # marketplace_policy
    "MARKETPLACE_ALIASES",
    "DEFAULT_TARGET_MARKETPLACES",
    "normalize_marketplace_code",
    "get_marketplace_variants",
    "filter_allowed_marketplaces",
    "validate_source_marketplace",
    # js_client
    "JungleScoutClient",
    "BudgetExhaustedError",
]
