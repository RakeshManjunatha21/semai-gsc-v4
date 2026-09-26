"""
SEMAI Analytics Intelligence Platform - Configuration.

Central configuration for API keys, model setup, and application constants.
"""

import os
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

from services.llm import GeminiModel

# =============================================================================
# Application Constants
# =============================================================================

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")


def get_setting(key: str, default=None):
    """Return configuration value from env first, then Streamlit secrets."""
    value = os.getenv(key)
    if value is not None and value != "":
        return value

    try:
        import streamlit as st

        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass

    return default

ADMIN_PASSWORD = get_setting("ADMIN_PASSWORD", "semai2026")

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/bigquery",
    "openid",
]


def resolve_redirect_uri() -> str:
    """Return the redirect URI for this runtime.

    Prefer the configured value from environment/secrets. When the app is
    running behind Streamlit Cloud or another proxy, derive the external host
    from the request headers so the OAuth callback matches the live URL instead
    of defaulting to localhost.
    """
    configured = get_setting("REDIRECT_URI")
    if configured:
        return configured.rstrip("/")

    try:
        import streamlit as st

        if hasattr(st, "context") and getattr(st, "context", None) is not None:
            headers = getattr(st.context, "headers", {}) or {}
            host = headers.get("Host") or headers.get("host")
            if host:
                forwarded_proto = (
                    headers.get("X-Forwarded-Proto")
                    or headers.get("x-forwarded-proto")
                )
                local_host = host.split(":", 1)[0] in {
                    "localhost", "127.0.0.1", "[::1]"
                }
                scheme = forwarded_proto or ("http" if local_host else "https")
                return f"{scheme}://{host}".rstrip("/")
    except Exception:
        pass

    return "http://localhost:8501"


REDIRECT_URI = resolve_redirect_uri()

TOKENS_DIR = get_setting("TOKENS_DIR", "tokens")
ENABLE_TOKEN_PERSISTENCE = str(
    get_setting("ENABLE_TOKEN_PERSISTENCE", "true")
).lower() == "true"

OAUTH_CLIENT_ID = get_setting("GOOGLE_OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = get_setting("GOOGLE_OAUTH_CLIENT_SECRET")
OAUTH_CLIENT_SECRET_JSON = get_setting("GOOGLE_OAUTH_CLIENT_SECRET_JSON")


# =============================================================================
# Gemini API Configuration
# =============================================================================

def load_gemini_api_key() -> str | None:
    """Load Gemini API key from environment variable or api_key file.

    Returns:
        The API key string, or ``None`` if not found.
    """
    api_key = get_setting("GOOGLE_GEMINI_KEY") or get_setting("GEMINI_API_KEY")

    if not api_key:
        api_key_file = BASE_DIR / "api_key"
        if api_key_file.exists():
            try:
                with open(api_key_file, "r") as fh:
                    for line in fh:
                        if "AIza" in line:
                            api_key = (
                                line.split('"')[1] if '"' in line else line.strip()
                            )
                            break
            except Exception:
                pass

    return api_key


GEMINI_API_KEY = load_gemini_api_key()
OPENROUTER_API_KEYS = tuple(dict.fromkeys(
    key
    for key in (
        get_setting("OPENROUTER_API_KEY"),
        get_setting("OPENROUTER_API_KEY_2"),
    )
    if key
))
OPENROUTER_API_KEY = OPENROUTER_API_KEYS[0] if OPENROUTER_API_KEYS else None
GEMINI_MODEL_NAME = get_setting("GEMINI_MODEL", "gemini-3-flash-preview")


def configure_model() -> GeminiModel | None:
    """Configure and return the Gemini generative model.

    Returns:
        A configured ``GenerativeModel`` instance, or ``None`` when the API
        key is unavailable.
    """
    if not GEMINI_API_KEY:
        return None

    genai.configure(api_key=GEMINI_API_KEY)

    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL_NAME,
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 8192,
        },
    )
    return GeminiModel(model)


MODEL = configure_model()
