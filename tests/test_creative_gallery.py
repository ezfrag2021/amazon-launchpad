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

MODULE_PATH = SERVICES_DIR / "creative_gallery.py"
SPEC = importlib.util.spec_from_file_location("services.creative_gallery", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {MODULE_PATH}")
_module = importlib.util.module_from_spec(SPEC)
sys.modules["services.creative_gallery"] = _module
SPEC.loader.exec_module(_module)

decode_inline_image = _module.decode_inline_image
encode_inline_image = _module.encode_inline_image


def test_inline_encode_decode_roundtrip() -> None:
    payload = b"\x89PNG\r\n\x1a\nmock"
    encoded = encode_inline_image(payload)
    decoded = decode_inline_image(encoded)
    assert decoded == payload


def test_inline_decode_invalid_returns_none() -> None:
    assert decode_inline_image("inline:base64,%%%") is None
    assert decode_inline_image("not-inline") is None
