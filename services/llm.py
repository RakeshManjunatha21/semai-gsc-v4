"""LLM provider adapters and model discovery."""

from __future__ import annotations

from dataclasses import dataclass

import requests


OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
OPENROUTER_FREE_ROUTER = "openrouter/free"


@dataclass
class _GeneratedContent:
    text: str


class OpenRouterError(RuntimeError):
    """A safe error that can be shown directly in the application UI."""


class OpenRouterModel:
    """Expose OpenRouter through the Gemini-style interface used by reports."""

    def __init__(self, api_key: str, model_name: str):
        self.api_key = api_key
        self.model_name = model_name

    def generate_content(self, prompt: str) -> _GeneratedContent:
        try:
            response = requests.post(
                f"{OPENROUTER_API_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://semai-gsc-v4.streamlit.app/",
                    "X-OpenRouter-Title": "SEMAI Analytics Intelligence",
                },
                json={
                    "model": self.model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 8192,
                },
                timeout=180,
            )
        except requests.RequestException as exc:
            raise OpenRouterError(
                "OpenRouter could not be reached. Try again shortly."
            ) from exc
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            messages = {
                401: "OpenRouter rejected the server API key. Replace it in Streamlit Secrets.",
                402: "OpenRouter requires credits for this request or model.",
                404: "The selected OpenRouter model is no longer available.",
                429: "OpenRouter is temporarily rate limited. Try again shortly.",
            }
            message = messages.get(
                response.status_code,
                f"OpenRouter request failed with status {response.status_code}.",
            )
            raise OpenRouterError(message) from exc
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise OpenRouterError("OpenRouter returned an invalid response.") from exc
        if not text:
            raise OpenRouterError("OpenRouter returned an empty response.")
        return _GeneratedContent(text=text)


def list_openrouter_free_models() -> list[dict[str, str]]:
    """Return the currently advertised zero-cost text models."""
    response = requests.get(f"{OPENROUTER_API_URL}/models", timeout=20)
    response.raise_for_status()
    models = []
    for model in response.json().get("data", []):
        pricing = model.get("pricing", {})
        if (
            str(pricing.get("prompt")) == "0"
            and str(pricing.get("completion")) == "0"
            and model.get("id")
        ):
            models.append({
                "id": model["id"],
                "name": model.get("name") or model["id"],
            })
    return sorted(models, key=lambda item: item["name"].lower())