"""
SEMAI Analytics Intelligence Platform - OAuth Authentication.

Handles Google OAuth 2.0 flow, credential persistence, refresh, and
user management. Pure Python — no Streamlit dependency.
"""

import os
import pickle
import re
import json
from pathlib import Path

from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import (
    SCOPES,
    REDIRECT_URI,
    TOKENS_DIR,
    ENABLE_TOKEN_PERSISTENCE,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_CLIENT_SECRET_JSON,
)


# =============================================================================
# Helpers
# =============================================================================

def _ensure_tokens_dir() -> None:
    """Create the tokens directory if it does not exist."""
    if not ENABLE_TOKEN_PERSISTENCE:
        return

    if not os.path.exists(TOKENS_DIR):
        os.makedirs(TOKENS_DIR)


_ensure_tokens_dir()


def get_persistence_mode() -> str:
    """Return credential persistence mode.

    Returns:
        ``"disk"`` when token files are enabled, else ``"memory"``.
    """
    return "disk" if ENABLE_TOKEN_PERSISTENCE else "memory"


def sanitize_filename(email: str) -> str:
    """Convert an email address to a safe filename.

    Args:
        email: The user's email address.

    Returns:
        A sanitised string safe for use as a filename component.
    """
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", email)


def get_token_path(email: str) -> str:
    """Return the file path used to store credentials for *email*.

    Args:
        email: The user's email address.

    Returns:
        Absolute path to the pickle token file.
    """
    safe_email = sanitize_filename(email)
    return os.path.join(TOKENS_DIR, f"token_{safe_email}.pickle")


# =============================================================================
# Credential Persistence
# =============================================================================

def save_credentials(creds, email: str) -> None:
    """Persist *creds* to disk for the given *email*.

    Args:
        creds: A Google ``Credentials`` object.
        email: The user's email address.
    """
    if not ENABLE_TOKEN_PERSISTENCE:
        return

    token_path = get_token_path(email)
    with open(token_path, "wb") as fh:
        pickle.dump(creds, fh)


def load_credentials(email: str):
    """Load persisted credentials for *email*.

    Args:
        email: The user's email address.

    Returns:
        A Google ``Credentials`` object or ``None`` if unavailable.
    """
    if not ENABLE_TOKEN_PERSISTENCE:
        return None

    token_path = get_token_path(email)
    if not os.path.exists(token_path):
        return None

    try:
        with open(token_path, "rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


def refresh_credentials(creds, email: str):
    """Refresh expired credentials and re-save them.

    Args:
        creds: A Google ``Credentials`` object.
        email: The user's email address.

    Returns:
        Refreshed credentials, or ``None`` on failure (token file is
        deleted in that case).
    """
    try:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Persist refreshed credentials in the active persistence mode
            # (disk for VM/local, memory for ephemeral cloud deployments).
            save_credentials(creds, email)
            return creds
        return creds
    except Exception:
        token_path = get_token_path(email)
        if os.path.exists(token_path):
            os.remove(token_path)
        return None


def delete_user_credentials(email: str) -> None:
    """Remove saved credentials for *email*.

    Args:
        email: The user's email address.
    """
    if not ENABLE_TOKEN_PERSISTENCE:
        return

    token_path = get_token_path(email)
    if os.path.exists(token_path):
        os.remove(token_path)


# =============================================================================
# User Discovery
# =============================================================================

def get_all_saved_users() -> list[str]:
    """Return a list of email addresses that have saved tokens.

    Returns:
        List of email strings.
    """
    if not ENABLE_TOKEN_PERSISTENCE:
        return []

    if not os.path.exists(TOKENS_DIR):
        return []

    users: list[str] = []
    for filename in os.listdir(TOKENS_DIR):
        if filename.startswith("token_") and filename.endswith(".pickle"):
            email = filename.replace("token_", "").replace(".pickle", "")
            email = email.replace("_", "@", 1)
            if email != "unknown" and "unknown" not in email.lower():
                users.append(email)
    return users


# =============================================================================
# OAuth Flow
# =============================================================================

def get_user_email(creds) -> str:
    """Extract the user's email from Google credentials.

    Args:
        creds: A Google ``Credentials`` object.

    Returns:
        Email address string.

    Raises:
        ValueError: If the email cannot be retrieved.
    """
    try:
        service = build("oauth2", "v2", credentials=creds)
        user_info = service.userinfo().get().execute()
        email = user_info.get("email")
        if not email:
            raise ValueError("Unable to retrieve user email from credentials")
        return email
    except Exception as exc:
        raise ValueError(f"Failed to get user email: {exc}") from exc


def build_flow() -> Flow:
    """Create and return a Google OAuth ``Flow`` instance.

    Returns:
        Configured ``Flow`` for the application.
    """
    if OAUTH_CLIENT_SECRET_JSON:
        client_config = json.loads(OAUTH_CLIENT_SECRET_JSON)
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )

    if OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET:
        client_config = {
            "web": {
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )

    # Local/VM fallback: use client_secret.json or discover downloaded files
    # such as client_secret_<client-id>.apps.googleusercontent.com.json.
    candidates = [
        Path("client_secret.json"),
        *sorted(Path(".").glob("client_secret*.json")),
    ]
    secret_file = next((p for p in candidates if p.exists()), None)

    if secret_file:
        return Flow.from_client_secrets_file(
            str(secret_file),
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )

    raise ValueError(
        "Google OAuth client configuration not found. "
        "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET "
        "(recommended for Streamlit Cloud) or provide client_secret.json locally."
    )
