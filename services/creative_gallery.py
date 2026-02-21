"""Creative image gallery persistence helpers."""

from __future__ import annotations

import base64
from typing import Any, Callable

from psycopg.rows import dict_row

INLINE_IMAGE_PREFIX = "inline:base64,"


def encode_inline_image(image_bytes: bytes) -> str:
    return f"{INLINE_IMAGE_PREFIX}{base64.b64encode(image_bytes).decode('ascii')}"


def decode_inline_image(storage_path: str | None) -> bytes | None:
    if not storage_path or not storage_path.startswith(INLINE_IMAGE_PREFIX):
        return None
    payload = storage_path[len(INLINE_IMAGE_PREFIX) :]
    try:
        return base64.b64decode(payload, validate=True)
    except Exception:
        return None


def image_gallery_supports_binary(
    open_conn: Callable[[], Any], logger: Any | None = None
) -> bool:
    try:
        with open_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'launchpad'
                          AND table_name = 'image_gallery'
                          AND column_name = 'image_bytes'
                    )
                    """
                )
                row = cur.fetchone()
                return bool(row and row[0])
    except Exception as exc:
        if logger is not None:
            logger.warning("Could not verify image_gallery schema: %s", exc)
        return False


def save_image_to_gallery(
    open_conn: Callable[[], Any],
    launch_id: int,
    slot_number: int,
    image_type: str,
    prompt_used: str,
    image_bytes: bytes | None,
    model_used: str,
    storage_path: str | None = None,
    supports_binary: bool = False,
    logger: Any | None = None,
) -> bool:
    try:
        storage_value = storage_path
        if not supports_binary and image_bytes:
            storage_value = encode_inline_image(image_bytes)

        with open_conn() as conn:
            with conn.cursor() as cur:
                if supports_binary:
                    cur.execute(
                        """
                        INSERT INTO launchpad.image_gallery
                            (launch_id, slot_number, image_type, prompt_used, storage_path, model_used, image_bytes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (launch_id, slot_number) DO UPDATE SET
                            image_type   = EXCLUDED.image_type,
                            prompt_used  = EXCLUDED.prompt_used,
                            storage_path = EXCLUDED.storage_path,
                            model_used   = EXCLUDED.model_used,
                            image_bytes  = EXCLUDED.image_bytes,
                            generated_at = now()
                        """,
                        (
                            launch_id,
                            slot_number,
                            image_type,
                            prompt_used,
                            storage_value,
                            model_used,
                            image_bytes,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO launchpad.image_gallery
                            (launch_id, slot_number, image_type, prompt_used, storage_path, model_used)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (launch_id, slot_number) DO UPDATE SET
                            image_type   = EXCLUDED.image_type,
                            prompt_used  = EXCLUDED.prompt_used,
                            storage_path = EXCLUDED.storage_path,
                            model_used   = EXCLUDED.model_used,
                            generated_at = now()
                        """,
                        (
                            launch_id,
                            slot_number,
                            image_type,
                            prompt_used,
                            storage_value,
                            model_used,
                        ),
                    )
            conn.commit()
            return True
    except Exception as exc:
        if logger is not None:
            logger.error("Image gallery save error: %s", exc)
        return False


def load_image_gallery(
    open_conn: Callable[[], Any],
    launch_id: int,
    supports_binary: bool = False,
    logger: Any | None = None,
) -> dict[int, dict[str, Any]]:
    try:
        with open_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if supports_binary:
                    cur.execute(
                        """
                        SELECT slot_number, image_type, prompt_used, storage_path, image_bytes,
                               model_used, generated_at
                        FROM launchpad.image_gallery
                        WHERE launch_id = %s
                        ORDER BY slot_number
                        """,
                        (launch_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT slot_number, image_type, prompt_used, storage_path,
                               NULL::bytea AS image_bytes, model_used, generated_at
                        FROM launchpad.image_gallery
                        WHERE launch_id = %s
                        ORDER BY slot_number
                        """,
                        (launch_id,),
                    )
                rows = cur.fetchall()
        gallery: dict[int, dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            maybe_image = data.get("image_bytes")
            if isinstance(maybe_image, memoryview):
                data["image_bytes"] = maybe_image.tobytes()
            elif not maybe_image:
                inline_image = decode_inline_image(data.get("storage_path"))
                if inline_image:
                    data["image_bytes"] = inline_image
            gallery[int(row["slot_number"])] = data
        return gallery
    except Exception as exc:
        if logger is not None:
            logger.warning("Could not load image gallery: %s", exc)
        return {}
