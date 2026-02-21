from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "services"

services_pkg = types.ModuleType("services")
services_pkg.__path__ = [str(SERVICES_DIR)]
sys.modules.setdefault("services", services_pkg)

MODULE_PATH = SERVICES_DIR / "imagen_quota.py"
SPEC = importlib.util.spec_from_file_location("services.imagen_quota", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {MODULE_PATH}")
_module = importlib.util.module_from_spec(SPEC)
sys.modules["services.imagen_quota"] = _module
SPEC.loader.exec_module(_module)

call_with_quota_retry = _module.call_with_quota_retry
is_quota_error = _module.is_quota_error
seconds_until_next_image_request = _module.seconds_until_next_image_request


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        return


def test_is_quota_error_detects_resource_exhausted() -> None:
    exc = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
    assert is_quota_error(exc)


def test_seconds_until_next_image_request_respects_quota_and_strict() -> None:
    session = {
        "cs_imagen_quota_cooldown_until": 9999999999.0,
        "cs_imagen_strict_spacing": True,
        "cs_imagen_last_request_at": 0.0,
    }
    remaining = seconds_until_next_image_request(
        session=session,
        strict_spacing_seconds=60.0,
        enforce_strict_spacing=True,
    )
    assert remaining > 0


def test_call_with_quota_retry_retries_then_succeeds() -> None:
    attempts = {"n": 0}
    session: dict[str, float] = {}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return "ok"

    result = call_with_quota_retry(
        op_name="test-op",
        fn=flaky,
        session=session,
        max_attempts=2,
        base_seconds=0.001,
        max_seconds=0.01,
        quota_cooldown_seconds=0.001,
        logger=_Logger(),
    )
    assert result == "ok"
    assert attempts["n"] == 2
