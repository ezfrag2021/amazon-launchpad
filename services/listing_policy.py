"""Listing policy normalization and blocked-term helpers."""

from __future__ import annotations

import re
from typing import Any

EU_UK_MARKETPLACES = {"UK", "DE", "FR", "IT", "ES"}

DEFAULT_EU_UK_RESTRICTED_MARKETING_PHRASES = [
    "physician recommended",
    "physician-recommended",
    "doctor recommended",
    "clinically proven",
    "clinically tested",
    "medical grade",
    "therapeutic",
    "heals",
    "cures",
    "treats",
    "prevents disease",
    "BPA free",
    "BPA-free",
    "phthalate free",
    "phthalate-free",
    "non toxic",
    "non-toxic",
    "chemical free",
    "chemical-free",
]

DEFAULT_GLOBAL_PROHIBITED_LISTING_TERMS = [
    "#1",
    "number one",
    "best seller",
    "guaranteed",
    "risk free",
    "100% safe",
    "FDA approved",
    "CE certified",
    "genuine",
    "authentic",
    "free shipping",
    "click here",
    "buy now",
    "limited time",
    "cancer",
    "arthritis",
    "diabetes",
    "covid",
    "aspirin",
    "ibuprofen",
    "tylenol",
    "nike",
    "adidas",
    "apple",
    "samsung",
    "amazon basics",
]


def split_phrase_lines(raw_text: str) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for line in (raw_text or "").splitlines():
        item = " ".join(line.strip().split())
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        phrases.append(item)
    return phrases


def normalize_policy_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scoped: dict[str, list[str]] = {"global": [], "eu_uk": []}
    for row in rows:
        scope = str(row.get("scope") or "").strip().lower()
        term = " ".join(str(row.get("term") or "").strip().split())
        if scope in scoped and term:
            scoped[scope].append(term)
    return scoped


def effective_blocked_phrases(
    marketplace: str,
    policy_terms: dict[str, Any],
    additional_terms_raw: str,
) -> list[str]:
    phrases = list(policy_terms.get("global", []))
    if marketplace in EU_UK_MARKETPLACES:
        phrases.extend(policy_terms.get("eu_uk", []))
    phrases.extend(split_phrase_lines(additional_terms_raw))

    deduped: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(phrase)
    return deduped


def strip_blocked_phrases(
    text: str, blocked_phrases: list[str]
) -> tuple[str, list[str]]:
    cleaned = str(text or "")
    removed: list[str] = []
    for phrase in sorted(blocked_phrases, key=len, reverse=True):
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", flags=re.IGNORECASE)
        if pattern.search(cleaned):
            cleaned = pattern.sub(" ", cleaned)
            removed.append(phrase)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, removed


def truncate_to_chars(text: str, max_chars: int) -> tuple[str, int]:
    value = str(text or "")
    if len(value) <= max_chars:
        return value, 0
    sliced = value[:max_chars]
    if " " in sliced and max_chars > 20:
        sliced = sliced.rsplit(" ", 1)[0].strip()
        if not sliced:
            sliced = value[:max_chars]
    return sliced, len(value) - len(sliced)


def truncate_to_utf8_bytes(text: str, max_bytes: int) -> tuple[str, int]:
    value = str(text or "")
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, 0
    out: list[str] = []
    used = 0
    for token in value.split():
        token_bytes = len(token.encode("utf-8"))
        spacer = 1 if out else 0
        if used + spacer + token_bytes > max_bytes:
            break
        if spacer:
            out.append(" ")
            used += 1
        out.append(token)
        used += token_bytes
    trimmed = "".join(out).strip()
    if not trimmed:
        trimmed = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return trimmed, len(encoded) - len(trimmed.encode("utf-8"))


def normalize_listing_with_policy(
    listing: dict[str, Any],
    marketplace: str,
    amazon_limits: dict[str, int],
    enforce_policy: bool,
    blocked_phrases: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = dict(listing or {})
    report: dict[str, Any] = {
        "removed_phrases": [],
        "truncated_fields": {},
        "marketplace": marketplace,
    }

    title = str(normalized.get("title") or "").strip()
    bullets_raw = normalized.get("bullets") or []
    if not isinstance(bullets_raw, list):
        bullets_raw = [str(bullets_raw)]
    bullets = [str(b or "").strip() for b in bullets_raw if str(b or "").strip()]
    while len(bullets) < 5:
        bullets.append("")
    bullets = bullets[:5]

    description = str(normalized.get("description") or "").strip()
    backend = str(normalized.get("backend_keywords") or "").replace(",", " ").strip()

    if enforce_policy:
        title, removed_title = strip_blocked_phrases(title, blocked_phrases)
        description, removed_desc = strip_blocked_phrases(description, blocked_phrases)
        backend, removed_backend = strip_blocked_phrases(backend, blocked_phrases)

        cleaned_bullets: list[str] = []
        removed_bullets: list[str] = []
        for bullet in bullets:
            clean_b, removed_b = strip_blocked_phrases(bullet, blocked_phrases)
            cleaned_bullets.append(clean_b)
            removed_bullets.extend(removed_b)
        bullets = cleaned_bullets

        removed_phrases = sorted(
            {
                p.lower()
                for p in removed_title
                + removed_desc
                + removed_backend
                + removed_bullets
            }
        )
        report["removed_phrases"] = removed_phrases

    title, title_trimmed = truncate_to_chars(title, amazon_limits["title"])
    if title_trimmed > 0:
        report["truncated_fields"]["title"] = title_trimmed

    desc, desc_trimmed = truncate_to_chars(description, amazon_limits["description"])
    description = desc
    if desc_trimmed > 0:
        report["truncated_fields"]["description"] = desc_trimmed

    limited_bullets: list[str] = []
    for idx, bullet in enumerate(bullets, 1):
        clipped, clipped_count = truncate_to_chars(bullet, amazon_limits["bullet"])
        limited_bullets.append(clipped)
        if clipped_count > 0:
            report["truncated_fields"][f"bullet_{idx}"] = clipped_count
    bullets = limited_bullets

    backend_tokens = []
    seen_tokens: set[str] = set()
    content_tokens = set(
        re.findall(r"[a-z0-9]+", f"{title} {' '.join(bullets)}".lower())
    )
    for token in backend.split():
        key = token.lower()
        if key in seen_tokens:
            continue
        if key in content_tokens:
            continue
        seen_tokens.add(key)
        backend_tokens.append(token)
    backend = " ".join(backend_tokens)
    backend, backend_trimmed = truncate_to_utf8_bytes(
        backend, amazon_limits["backend_keywords"]
    )
    if backend_trimmed > 0:
        report["truncated_fields"]["backend_keywords_bytes"] = backend_trimmed

    normalized["title"] = title
    normalized["bullets"] = bullets
    normalized["description"] = description
    normalized["backend_keywords"] = backend
    return normalized, report
