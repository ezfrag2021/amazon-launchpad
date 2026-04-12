"""
Google Generative AI authentication manager.

Provides service account key resolution and google.generativeai client
configuration for Gemini model usage in the Creative Studio (Stage 4).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from google.oauth2 import service_account

# Default key filename shared with amazon-mi
_DEFAULT_KEY_FILENAME = "gen-lang-client-0422857398-6a11b7435ae6.json"

# Required OAuth scope for Generative Language API
_GENERATIVE_LANGUAGE_SCOPE = "https://www.googleapis.com/auth/generative-language"


def resolve_service_account_key_path() -> Path:
    """Return the path to the Google service account JSON key file.

    Resolution order:
    1. ``GOOGLE_SERVICE_ACCOUNT_JSON`` environment variable (absolute or relative path)
    2. Default filename ``gen-lang-client-0422857398-6a11b7435ae6.json`` located
       next to this file's parent directory (repo root).
    """
    env_value = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if env_value and env_value != "<GOOGLE_SERVICE_ACCOUNT_JSON_PATH>":
        return Path(env_value)

    # Fallback: repo root (two levels up from services/)
    repo_root = Path(__file__).parent.parent
    return repo_root / _DEFAULT_KEY_FILENAME


def get_vertex_genai_client(location: str = "us-central1") -> Any:
    """Return a google.genai Client configured for Vertex AI.

    Uses the service account key resolved by :func:`resolve_service_account_key_path`
    to authenticate against Vertex AI.  The project ID is read from the key file's
    ``project_id`` field or from the ``GOOGLE_CLOUD_PROJECT`` / ``GCLOUD_PROJECT``
    environment variables (first match wins).

    Args:
        location: Google Cloud region for Vertex AI endpoints (default ``us-central1``).

    Returns:
        A configured ``google.genai.Client`` instance with ``vertexai=True``.

    Raises:
        FileNotFoundError: If the service account key file does not exist.
        ImportError: If the google-genai package is not installed.
    """
    try:
        from google import genai  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-genai is not installed. Run: pip install 'google-genai>=1,<2'"
        ) from exc

    key_path = resolve_service_account_key_path()

    if not key_path.exists():
        raise FileNotFoundError(
            f"Google service account key not found at '{key_path}'. "
            "Set the GOOGLE_SERVICE_ACCOUNT_JSON environment variable to the correct path."
        )

    import json as _json

    with open(key_path) as fh:
        key_data = _json.load(fh)

    project_id = (
        key_data.get("project_id")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )

    credentials = service_account.Credentials.from_service_account_file(
        str(key_path),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )

    return genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        credentials=credentials,
    )


def get_generative_client() -> Any:
    """Configure google.generativeai with service account credentials and return the module.

    Credentials are loaded lazily (not at import time) using the service account
    key resolved by :func:`resolve_service_account_key_path`.

    Returns:
        The configured ``google.generativeai`` module, ready for model calls.

    Raises:
        FileNotFoundError: If the service account key file does not exist.
        google.auth.exceptions.TransportError: On credential refresh failures.
        ImportError: If google-generativeai package is not installed.
    """
    try:
        import google.generativeai as genai  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-generativeai is not installed. "
            "Run: pip install 'google-generativeai>=0.3,<1'"
        ) from exc

    key_path = resolve_service_account_key_path()

    if not key_path.exists():
        raise FileNotFoundError(
            f"Google service account key not found at '{key_path}'. "
            "Set the GOOGLE_SERVICE_ACCOUNT_JSON environment variable to the correct path."
        )

    credentials = service_account.Credentials.from_service_account_file(
        str(key_path),
        scopes=[_GENERATIVE_LANGUAGE_SCOPE],
    )

    genai.configure(credentials=credentials)
    return genai
