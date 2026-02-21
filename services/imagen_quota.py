"""Quota pacing helpers for Imagen requests."""

from __future__ import annotations

import random
import re
import time
from typing import Any, Callable


def is_quota_error(exc: Exception) -> bool:
    err = str(exc).upper()
    return (
        "RESOURCE_EXHAUSTED" in err
        or "TOO MANY REQUESTS" in err
        or "RATE LIMIT" in err
        or "QUOTA" in err
        or "429" in err
    )


def extract_retry_after_seconds(exc: Exception) -> float | None:
    match = re.search(r"RETRY[-_ ]?AFTER[^0-9]*([0-9]+(?:\.[0-9]+)?)", str(exc), re.I)
    if not match:
        return None
    try:
        return max(0.0, float(match.group(1)))
    except Exception:
        return None


def seconds_until_next_image_request(
    session: Any,
    strict_spacing_seconds: float,
    enforce_strict_spacing: bool = False,
) -> int:
    now = time.time()
    quota_until = float(session.get("cs_imagen_quota_cooldown_until", 0.0) or 0.0)
    strict_enabled = enforce_strict_spacing and bool(
        session.get("cs_imagen_strict_spacing", True)
    )
    strict_remaining = 0.0
    if strict_enabled:
        last_call = float(session.get("cs_imagen_last_request_at", 0.0) or 0.0)
        strict_remaining = max(0.0, strict_spacing_seconds - (now - last_call))
    return int(max(0.0, quota_until - now, strict_remaining))


def mark_imagen_request_attempt(session: Any) -> None:
    session["cs_imagen_last_request_at"] = time.time()


def call_with_quota_retry(
    op_name: str,
    fn: Callable[[], Any],
    session: Any,
    max_attempts: int,
    base_seconds: float,
    max_seconds: float,
    quota_cooldown_seconds: float,
    logger: Any,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_quota_error(exc) or attempt >= max_attempts:
                raise

            retry_after = extract_retry_after_seconds(exc)
            backoff = min(
                max_seconds,
                base_seconds * (2**attempt) * (1.0 + random.random() * 0.25),
            )
            delay_seconds = max(retry_after or 0.0, backoff, quota_cooldown_seconds)
            session["cs_imagen_quota_cooldown_until"] = time.time() + delay_seconds
            logger.warning(
                "Quota/rate limit for %s (attempt %s/%s). Retrying in %.2fs: %s",
                op_name,
                attempt + 1,
                max_attempts + 1,
                delay_seconds,
                exc,
            )
            time.sleep(delay_seconds)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{op_name} failed before request execution")
