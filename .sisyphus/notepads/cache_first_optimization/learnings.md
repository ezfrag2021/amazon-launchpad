- Added isolated cache-first unit tests by loading `services/js_client.py` via `importlib.util.spec_from_file_location` to avoid importing `services/__init__.py` and unrelated optional dependencies during test collection.
- For cache-hit verification, asserting `reserve_budget.assert_not_called()` plus `fetch_func.assert_not_called()` confirms zero paid-call path.
- TTL behavior is reliably validated by asserting argument index `6` in `launchpad.set_js_cache(..., ttl_hours)` SQL parameter tuple.

## [2026-02-19] Migration 008 Applied Successfully
- Status: ✅ EXECUTED
- Applied by: amazon_dash_user superuser
- Schema changes: request_key column added, unique constraint updated to (asin, marketplace, endpoint, request_key)
- Functions updated: get_js_cache(4 params), set_js_cache(7 params)
- Integration test: PASSED - cache SET/GET/MISS working correctly
- Fixed: Parameter order bug in services/js_client.py (api_calls_used and request_key were swapped)
- All 7 unit tests: PASSING

