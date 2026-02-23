"""SP-API helpers for fee estimation in pricing workflows."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _load_optional_external_env() -> None:
    """Best-effort load of shared SP-API env files.

    Launchpad typically uses /mnt/amazon-launch/.env, but SP-API credentials may
    exist in sibling projects (for example /mnt/amazon-bi/.env).
    """
    try:
        from dotenv import load_dotenv  # type: ignore[import]
    except Exception:  # noqa: BLE001
        return

    candidates = [
        Path("/mnt/amazon-bi/.env"),
        Path("/mnt/amazon-bi/.env.local"),
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)


def _resolve_marketplace_enum(code: str) -> Any:
    from sp_api.base import Marketplaces  # type: ignore[import]

    normalized = (code or "").strip().upper()
    if normalized == "UK":
        normalized = "GB"
    if not normalized or not hasattr(Marketplaces, normalized):
        raise ValueError(f"Unsupported SP-API marketplace code: {code}")
    return getattr(Marketplaces, normalized)


def _currency_for_marketplace(code: str) -> str:
    normalized = (code or "").strip().upper()
    if normalized in {"UK", "GB"}:
        return "GBP"
    if normalized in {"DE", "FR", "IT", "ES", "NL", "IE", "BE", "PL", "SE"}:
        return "EUR"
    if normalized in {"US", "CA"}:
        return "USD"
    return "USD"


def _extract_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = (
            value.replace("£", "")
            .replace("€", "")
            .replace("$", "")
            .replace(",", "")
            .strip()
        )
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("Amount", "amount", "value", "price"):
            parsed = _extract_numeric(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _normalize_asin_for_spapi(raw_asin: str, marketplace: str) -> str:
    asin = "".join(ch for ch in str(raw_asin or "").upper() if ch.isalnum())
    if len(asin) == 10:
        return asin

    # Jungle Scout payloads sometimes include marketplace prefix (e.g., UKB0098ABVI8).
    # If that pattern is present, strip the 2-letter marketplace code.
    if len(asin) == 12 and re.match(r"^[A-Z]{2}[A-Z0-9]{10}$", asin):
        tail = asin[2:]
        if len(tail) == 10:
            return tail

    # As a final guardrail, allow extracting a 10-char ASIN-like token from longer strings.
    match = re.search(r"([A-Z0-9]{10})", asin)
    if match:
        return match.group(1)

    return asin


def _dig_referral_pct(payload: Any) -> float | None:
    if isinstance(payload, dict):
        ident = str(payload.get("FeeType") or payload.get("feeType") or "").upper()
        if "REFERRAL" in ident:
            for key in ("FeePromotion", "FinalFee", "fee", "Rate", "rate"):
                parsed = _extract_numeric(payload.get(key))
                if parsed is not None:
                    if key.lower() in {"rate", "feepromotion"} and parsed <= 1.0:
                        return parsed * 100.0
                    return parsed

        for key in ("FeeDetailList", "feeDetailList", "FeesEstimate", "feesEstimate"):
            parsed = _dig_referral_pct(payload.get(key))
            if parsed is not None:
                return parsed

        for key in ("payload", "FeesEstimateResult", "feesEstimateResult"):
            parsed = _dig_referral_pct(payload.get(key))
            if parsed is not None:
                return parsed

        for value in payload.values():
            parsed = _dig_referral_pct(value)
            if parsed is not None:
                return parsed

    if isinstance(payload, list):
        for item in payload:
            parsed = _dig_referral_pct(item)
            if parsed is not None:
                return parsed
    return None


def _dig_fba_fee(payload: Any) -> float | None:
    if isinstance(payload, dict):
        ident = str(payload.get("FeeType") or payload.get("feeType") or "").upper()
        if "FBA" in ident or "FULFILLMENT" in ident or "PICK" in ident:
            for key in ("FinalFee", "finalFee", "FeeAmount", "feeAmount", "fee"):
                parsed = _extract_numeric(payload.get(key))
                if parsed is not None:
                    return parsed

        for key in (
            "TotalFeesEstimate",
            "totalFeesEstimate",
            "FeeDetailList",
            "feeDetailList",
            "FeesEstimate",
            "feesEstimate",
            "payload",
            "FeesEstimateResult",
            "feesEstimateResult",
        ):
            parsed = _dig_fba_fee(payload.get(key))
            if parsed is not None:
                return parsed

        for value in payload.values():
            parsed = _dig_fba_fee(value)
            if parsed is not None:
                return parsed

    if isinstance(payload, list):
        for item in payload:
            parsed = _dig_fba_fee(item)
            if parsed is not None:
                return parsed
    return None


def _dig_api_errors(payload: Any) -> list[str]:
    errors: list[str] = []
    if isinstance(payload, dict):
        for key in ("Errors", "errors", "Error", "error"):
            val = payload.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        code = str(item.get("Code") or item.get("code") or "")
                        msg = str(item.get("Message") or item.get("message") or "")
                        text = f"{code}: {msg}".strip(": ")
                        if text:
                            errors.append(text)
                    elif item:
                        errors.append(str(item))
            elif isinstance(val, dict):
                code = str(val.get("Code") or val.get("code") or "")
                msg = str(val.get("Message") or val.get("message") or "")
                text = f"{code}: {msg}".strip(": ")
                if text:
                    errors.append(text)

        for sub_val in payload.values():
            errors.extend(_dig_api_errors(sub_val))
    elif isinstance(payload, list):
        for item in payload:
            errors.extend(_dig_api_errors(item))
    return errors


def _payload_shape(payload: Any) -> str:
    if isinstance(payload, dict):
        keys = sorted(str(k) for k in payload.keys())
        return "{" + ", ".join(keys[:8]) + (", ..." if len(keys) > 8 else "") + "}"
    if isinstance(payload, list):
        return f"list(len={len(payload)})"
    return type(payload).__name__


def estimate_competitor_fees(
    competitor_offers: list[dict[str, Any]],
    marketplace: str,
    max_offers: int = 10,
) -> dict[str, Any]:
    """Estimate median referral % and fulfillment fee from SP-API fee estimates."""
    _load_optional_external_env()

    try:
        from sp_api.api import ProductFees  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "python-amazon-sp-api is not installed. "
            "Run: pip install 'python-amazon-sp-api>=1.9,<2'"
        ) from exc

    refresh_token = _env_first(
        "SP_API_REFRESH_TOKEN",
        "SPAPI_REFRESH_TOKEN",
        "AMZ_SP_API_REFRESH_TOKEN",
    )
    lwa_app_id = _env_first("SP_API_LWA_APP_ID", "SPAPI_LWA_CLIENT_ID", "LWA_APP_ID")
    lwa_client_secret = _env_first(
        "SP_API_LWA_CLIENT_SECRET",
        "SPAPI_LWA_CLIENT_SECRET",
        "LWA_CLIENT_SECRET",
    )
    aws_access_key = _env_first("SP_API_AWS_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
    aws_secret_key = _env_first("SP_API_AWS_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
    role_arn = _env_first("SP_API_ROLE_ARN")

    missing = []
    if not refresh_token:
        missing.append("SP_API_REFRESH_TOKEN / SPAPI_REFRESH_TOKEN")
    if not lwa_app_id:
        missing.append("SP_API_LWA_APP_ID / SPAPI_LWA_CLIENT_ID / LWA_APP_ID")
    if not lwa_client_secret:
        missing.append(
            "SP_API_LWA_CLIENT_SECRET / SPAPI_LWA_CLIENT_SECRET / LWA_CLIENT_SECRET"
        )
    if not aws_access_key:
        missing.append("SP_API_AWS_ACCESS_KEY / AWS_ACCESS_KEY_ID")
    if not aws_secret_key:
        missing.append("SP_API_AWS_SECRET_KEY / AWS_SECRET_ACCESS_KEY")
    if missing:
        raise RuntimeError(
            "SP-API credentials not configured. Missing: " + ", ".join(missing)
        )

    assert refresh_token is not None
    assert lwa_app_id is not None
    assert lwa_client_secret is not None
    assert aws_access_key is not None
    assert aws_secret_key is not None

    marketplace_enum = _resolve_marketplace_enum(marketplace)
    currency = _currency_for_marketplace(marketplace)

    credentials: dict[str, str] = {
        "refresh_token": refresh_token,
        "lwa_app_id": lwa_app_id,
        "lwa_client_secret": lwa_client_secret,
        "aws_access_key": aws_access_key,
        "aws_secret_key": aws_secret_key,
    }
    if role_arn:
        credentials["role_arn"] = role_arn

    fees_api = ProductFees(marketplace=marketplace_enum, credentials=credentials)

    referral_pcts: list[float] = []
    fulfillment_fees: list[float] = []
    sample_count = 0
    errors: list[str] = []
    api_errors: list[str] = []
    unparsable_shapes: list[str] = []

    for offer in competitor_offers[:max_offers]:
        asin_raw = str(offer.get("asin") or "").strip().upper()
        asin = _normalize_asin_for_spapi(asin_raw, marketplace)
        price = _extract_numeric(offer.get("price"))
        if not asin or price is None or price <= 0:
            continue

        sample_count += 1
        try:
            resp = fees_api.get_product_fees_estimate_for_asin(
                asin=asin,
                price=price,
                currency=currency,
                is_fba=True,
            )
            payload = getattr(resp, "payload", resp)
            api_err = _dig_api_errors(payload)
            for item in api_err:
                if len(api_errors) < 5:
                    api_errors.append(f"{asin}: {item}")

            referral = _dig_referral_pct(payload)
            fba_fee = _dig_fba_fee(payload)

            if referral is None and fba_fee is None and len(unparsable_shapes) < 5:
                unparsable_shapes.append(f"{asin}: payload={_payload_shape(payload)}")

            if referral is not None and referral > 0:
                referral_pcts.append(float(referral))
            if fba_fee is not None and fba_fee >= 0:
                fulfillment_fees.append(float(fba_fee))
        except Exception as exc:  # noqa: BLE001
            if len(errors) < 3:
                errors.append(f"{asin}: {exc}")

    if sample_count == 0:
        raise RuntimeError(
            "No valid competitor ASIN/price pairs available for SP-API fee estimation."
        )

    if not referral_pcts and not fulfillment_fees:
        detail_parts: list[str] = []
        if errors:
            detail_parts.append("request errors: " + " | ".join(errors))
        if api_errors:
            detail_parts.append("api errors: " + " | ".join(api_errors))
        if unparsable_shapes:
            detail_parts.append("unparsed payloads: " + " | ".join(unparsable_shapes))
        detail = f" Details: {' || '.join(detail_parts)}" if detail_parts else ""
        raise RuntimeError(
            f"SP-API returned no usable fee estimates after {sample_count} samples."
            + detail
        )

    referral_median = (
        sorted(referral_pcts)[len(referral_pcts) // 2] if referral_pcts else None
    )
    fulfillment_median = (
        sorted(fulfillment_fees)[len(fulfillment_fees) // 2]
        if fulfillment_fees
        else None
    )

    return {
        "referral_fee_pct": referral_median,
        "fulfillment_fee": fulfillment_median,
        "sample_count": sample_count,
        "referral_samples": len(referral_pcts),
        "fulfillment_samples": len(fulfillment_fees),
    }
