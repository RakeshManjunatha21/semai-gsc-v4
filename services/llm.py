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

    MAX_CONTINUATIONS = 2

    def __init__(self, api_key: str, model_name: str):
        self.api_key = api_key
        self.model_name = model_name

    def generate_content(self, prompt: str) -> _GeneratedContent:
        messages = [{"role": "user", "content": prompt}]
        text_parts = []
        for continuation in range(self.MAX_CONTINUATIONS + 1):
            text, finish_reason = self._generate_part(messages)
            if text:
                text_parts.append(text.rstrip())
            if finish_reason != "length":
                return _GeneratedContent(text="\n\n".join(text_parts).strip())
            if not text:
                raise OpenRouterError(
                    "The selected model used its output budget before producing "
                    "report text. Choose another free model."
                )
            if continuation == self.MAX_CONTINUATIONS:
                raise OpenRouterError(
                    "The selected model could not complete this report within "
                    "three responses. Choose a model with a larger output limit."
                )
            messages.extend([
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "Continue from the exact point where the report stopped. "
                        "Do not repeat prior content, restart the report, or add "
                        "commentary. Complete every remaining required section."
                    ),
                },
            ])

        raise OpenRouterError("OpenRouter could not complete the report.")

    def _generate_part(self, messages: list[dict[str, str]]) -> tuple[str, str | None]:
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
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 16384,
                    "reasoning": {
                        "effort": "low",
                        "exclude": True,
                    },
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
            choice = payload["choices"][0]
            message = choice["message"]
            text = message.get("content")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise OpenRouterError("OpenRouter returned an invalid response.") from exc
        if not text:
            if choice.get("error"):
                raise OpenRouterError(
                    "The selected OpenRouter model failed. Choose another free model."
                )
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise OpenRouterError(
                    "The selected model used its output budget before producing "
                    "report text. Choose another free model."
                )
            if finish_reason == "content_filter":
                raise OpenRouterError(
                    "The selected model filtered the response. Choose another free model."
                )
            raise OpenRouterError(
                "The selected OpenRouter model returned no report text. "
                "Choose another free model."
            )
        return text, choice.get("finish_reason")

    def test_connection(self) -> str:
        """Run a minimal completion through the selected backend model."""
        result = self.generate_content("Reply with exactly: OK")
        return result.text


def list_openrouter_free_models() -> list[dict[str, object]]:
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
            top_provider = model.get("top_provider") or {}
            models.append({
                "id": model["id"],
                "name": model.get("name") or model["id"],
                "context_length": top_provider.get("context_length")
                or model.get("context_length"),
                "max_completion_tokens": top_provider.get(
                    "max_completion_tokens"
                ),
            })
    return sorted(models, key=lambda item: item["name"].lower())