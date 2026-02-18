"""
Database connection utilities for amazon-launchpad.

Provides DSN resolution with three-tier env fallback, URL-encoding normalization,
role injection, and a psycopg connection helper.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, quote_plus, urlunparse

import psycopg

# Default role used for all launchpad DB connections
DEFAULT_ROLE = "launchpad_app"


def resolve_dsn(primary_var: str, *fallback_vars: str) -> str:
    """Resolve a DSN from environment variables using a three-tier fallback.

    Tries *primary_var* first, then each of *fallback_vars* in order.
    Raises ``RuntimeError`` if none of the variables are set.

    Args:
        primary_var: Name of the primary environment variable.
        *fallback_vars: Additional variable names tried in order.

    Returns:
        The first non-empty DSN string found.

    Raises:
        RuntimeError: When no variable resolves to a non-empty value.

    Example::

        dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
    """
    for var in (primary_var, *fallback_vars):
        value = os.getenv(var)
        if value:
            return value

    checked = ", ".join((primary_var, *fallback_vars))
    raise RuntimeError(
        f"No database DSN found. Checked environment variables: {checked}"
    )


def normalize_dsn(dsn: str) -> str:
    """URL-encode the password component of *dsn* to handle special characters.

    Parses the DSN, applies ``urllib.parse.quote_plus`` to the password, then
    reconstructs the URL.  If the DSN has no password the original string is
    returned unchanged.

    Args:
        dsn: A PostgreSQL connection string (``postgresql://user:pass@host/db``).

    Returns:
        DSN with the password percent-encoded.
    """
    parsed = urlparse(dsn)
    if not parsed.password:
        return dsn

    safe_password = quote_plus(parsed.password)
    # Reconstruct netloc with encoded password
    port_part = f":{parsed.port}" if parsed.port else ""
    netloc = f"{parsed.username}:{safe_password}@{parsed.hostname}{port_part}"
    normalized = parsed._replace(netloc=netloc)
    return urlunparse(normalized)


def inject_role(dsn: str, role: str) -> str:
    """Append ``?options=-c role=<role>`` to *dsn* if not already present.

    If the DSN already contains an ``options=`` query parameter the DSN is
    returned unchanged to avoid duplicating or overriding existing options.

    Args:
        dsn: PostgreSQL connection string.
        role: PostgreSQL role name to set via ``SET ROLE``.

    Returns:
        DSN with role option appended when not already present.
    """
    if "options=" in dsn:
        return dsn

    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-c role={role}"


def connect(
    dsn: str,
    role: str = DEFAULT_ROLE,
    read_only: bool = False,
) -> psycopg.Connection:
    """Open a psycopg connection with role and optional read-only mode.

    Connection defaults:
    - ``sslmode=disable`` (internal network; no TLS overhead)
    - Session role set via ``SET ROLE <role>``
    - Transaction read-only when *read_only* is ``True``

    Args:
        dsn: PostgreSQL connection string.  Should already be normalized
            (see :func:`normalize_dsn`).
        role: PostgreSQL role to activate after connecting.
            Defaults to :data:`DEFAULT_ROLE`.
        read_only: When ``True``, sets the session to read-only mode.

    Returns:
        An open :class:`psycopg.Connection` instance.

    Example::

        dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
        dsn = normalize_dsn(dsn)
        with connect(dsn, role="launchpad_app", read_only=True) as conn:
            ...
    """
    # Ensure sslmode=disable when not already specified
    if "sslmode=" not in dsn:
        separator = "&" if "?" in dsn else "?"
        dsn = f"{dsn}{separator}sslmode=disable"

    conn = psycopg.connect(dsn)

    with conn.cursor() as cur:
        cur.execute(f"SET ROLE {role}")
        if read_only:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")

    conn.commit()
    return conn
