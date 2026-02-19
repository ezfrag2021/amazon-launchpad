from __future__ import annotations

# pyright: basic, reportMissingImports=false

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "services" / "js_client.py"
SPEC = importlib.util.spec_from_file_location("js_client_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module at {MODULE_PATH}")
_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(_module)
JungleScoutClient = _module.JungleScoutClient


@pytest.fixture
def client() -> JungleScoutClient:
    instance = JungleScoutClient.__new__(JungleScoutClient)
    instance._client = MagicMock()
    return instance


@pytest.fixture
def mock_conn() -> MagicMock:
    conn = MagicMock(name="mock_psycopg_connection")
    cursor_ctx = MagicMock(name="mock_cursor_ctx")
    cursor = MagicMock(name="mock_cursor")

    cursor_ctx.__enter__.return_value = cursor
    cursor_ctx.__exit__.return_value = False
    conn.cursor.return_value = cursor_ctx

    return conn


def _cursor_from_conn(conn: MagicMock) -> MagicMock:
    return conn.cursor.return_value.__enter__.return_value


class TestRequestKeyGeneration:
    def test_generate_request_key_is_deterministic_for_param_order(
        self, client: JungleScoutClient
    ) -> None:
        params1 = {"asin": "B08N5WRWNW", "marketplace": "UK"}
        params2 = {"marketplace": "UK", "asin": "B08N5WRWNW"}

        assert client._generate_request_key(params1) == client._generate_request_key(params2)

    def test_generate_request_key_changes_for_different_params(
        self, client: JungleScoutClient
    ) -> None:
        params1 = {"asin": "B08N5WRWNW", "marketplace": "UK"}
        params2 = {"asin": "B08N5WRWNW", "marketplace": "DE"}

        assert client._generate_request_key(params1) != client._generate_request_key(params2)


class TestCacheFirstLogic:
    def test_get_cached_or_fetch_cache_hit_returns_cached_without_budget_use(
        self,
        client: JungleScoutClient,
        mock_conn: MagicMock,
    ) -> None:
        cursor = _cursor_from_conn(mock_conn)
        cursor.fetchone.return_value = ('{"source": "cache", "value": 123}',)

        client.reserve_budget = Mock(return_value=True)
        fetch_func = Mock(return_value={"source": "api"})

        result = client.get_cached_or_fetch(
            conn=mock_conn,
            endpoint="sales_estimates",
            params={"asin": "B08N5WRWNW", "marketplace": "UK"},
            fetch_func=fetch_func,
            script_name="test_script",
        )

        assert result == {"source": "cache", "value": 123}
        fetch_func.assert_not_called()
        client.reserve_budget.assert_not_called()
        mock_conn.commit.assert_not_called()
        assert cursor.execute.call_count == 1

    def test_get_cached_or_fetch_cache_miss_calls_api_and_stores_result(
        self,
        client: JungleScoutClient,
        mock_conn: MagicMock,
    ) -> None:
        cursor = _cursor_from_conn(mock_conn)
        cursor.fetchone.return_value = (None,)

        client.reserve_budget = Mock(return_value=True)
        fetch_result = {"source": "api", "value": 456}
        fetch_func = Mock(return_value=fetch_result)

        result = client.get_cached_or_fetch(
            conn=mock_conn,
            endpoint="sales_estimates",
            params={"asin": "B08N5WRWNW", "marketplace": "UK"},
            fetch_func=fetch_func,
            ttl_hours=12,
            script_name="test_script",
            launch_id=42,
        )

        assert result == fetch_result
        fetch_func.assert_called_once_with()
        client.reserve_budget.assert_called_once_with(
            mock_conn,
            "test_script",
            "sales_estimates",
            marketplace="UK",
            launch_id=42,
        )
        mock_conn.commit.assert_called_once_with()

        set_cache_call_args = cursor.execute.call_args_list[1][0]
        assert "launchpad.set_js_cache" in set_cache_call_args[0]
        assert set_cache_call_args[1][0] == "B08N5WRWNW"
        assert set_cache_call_args[1][1] == "UK"
        assert set_cache_call_args[1][2] == "sales_estimates"
        assert set_cache_call_args[1][6] == 12

    def test_ttl_hours_defaults_to_24_when_not_specified(
        self,
        client: JungleScoutClient,
        mock_conn: MagicMock,
    ) -> None:
        cursor = _cursor_from_conn(mock_conn)
        cursor.fetchone.return_value = (None,)

        client.reserve_budget = Mock(return_value=True)

        client.get_cached_or_fetch(
            conn=mock_conn,
            endpoint="keywords_by_asin",
            params={"asin": "B08N5WRWNW", "marketplace": "UK"},
            fetch_func=Mock(return_value={"ok": True}),
        )

        set_cache_call_args = cursor.execute.call_args_list[1][0]
        assert set_cache_call_args[1][6] == 24

    def test_get_cached_or_fetch_returns_none_when_budget_cannot_be_reserved(
        self,
        client: JungleScoutClient,
        mock_conn: MagicMock,
    ) -> None:
        cursor = _cursor_from_conn(mock_conn)
        cursor.fetchone.return_value = (None,)

        client.reserve_budget = Mock(return_value=False)
        fetch_func = Mock(return_value={"should": "not-run"})

        result = client.get_cached_or_fetch(
            conn=mock_conn,
            endpoint="sales_estimates",
            params={"asin": "B08N5WRWNW", "marketplace": "UK"},
            fetch_func=fetch_func,
            script_name="test_script",
        )

        assert result is None
        fetch_func.assert_not_called()
        mock_conn.commit.assert_not_called()
        assert cursor.execute.call_count == 1

    def test_use_cache_false_uses_no_cache_path(
        self,
        client: JungleScoutClient,
        mock_conn: MagicMock,
    ) -> None:
        client.get_cached_or_fetch = Mock(return_value={"should": "not-be-used"})
        client._get_sales_estimates_no_cache = Mock(return_value={"source": "no-cache"})

        result = client.get_sales_estimates(
            conn=mock_conn,
            asin="B08N5WRWNW",
            marketplace="UK",
            script_name="test_script",
            launch_id=99,
            use_cache=False,
        )

        assert result == {"source": "no-cache"}
        client._get_sales_estimates_no_cache.assert_called_once_with(
            mock_conn,
            "B08N5WRWNW",
            "UK",
            script_name="test_script",
            launch_id=99,
        )
        client.get_cached_or_fetch.assert_not_called()
