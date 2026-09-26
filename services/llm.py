"""LLM provider adapters and model discovery."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import requests


OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
OPENROUTER_FREE_ROUTER = "openrouter/free"
OPENROUTER_REQUEST_OUTPUT_TOKENS = 16_384
OPENROUTER_MIN_CONTEXT_TOKENS = 65_536
_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class _GeneratedContent:
    text: str


class OpenRouterError(RuntimeError):
    """A safe error that can be shown directly in the application UI."""

    def __init__(
        self,
        message: str,
        *,
        model_unavailable: bool = False,
        temporary: bool = False,
    ):
        super().__init__(message)
        self.model_unavailable = model_unavailable
        self.temporary = temporary


class GeminiError(RuntimeError):
    """A safe Gemini error that can be shown directly in the UI."""


class GeminiModel:
    """Add retries, deadlines, and continuation to a Gemini model."""

    MAX_CONTINUATIONS = 5
    MAX_RETRIES = 3

    def __init__(self, model, request_timeout: int = 180):
        self._model = model
        self.request_timeout = request_timeout

    @staticmethod
    def _finish_reason(response) -> str:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        finish_reason = getattr(candidates[0], "finish_reason", "")
        return str(getattr(finish_reason, "name", finish_reason)).upper()

    @staticmethod
    def _safe_text(response) -> str:
        try:
            return response.text or ""
        except (AttributeError, ValueError):
            return ""

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        return exc.__class__.__name__ in {
            "BadGateway",
            "DeadlineExceeded",
            "GatewayTimeout",
            "InternalServerError",
            "ResourceExhausted",
            "ServiceUnavailable",
            "TooManyRequests",
        }

    @staticmethod
    def _error_message(exc: Exception) -> str:
        error_name = exc.__class__.__name__
        if error_name in {"ResourceExhausted", "TooManyRequests"}:
            return (
                "Gemini's request quota is currently exhausted after automatic "
                "retries. Wait for the quota to reset or check the Gemini API "
                "billing and rate-limit settings."
            )
        if error_name in {"DeadlineExceeded", "GatewayTimeout"}:
            return (
                "Gemini did not finish before the request deadline after "
                "automatic retries. Try the report again shortly."
            )
        if error_name in {"PermissionDenied", "Unauthenticated"}:
            return (
                "Gemini rejected the server API key. Replace it in Streamlit "
                "Secrets and confirm the Generative Language API is enabled."
            )
        if error_name in {"InvalidArgument", "NotFound"}:
            return (
                "The configured Gemini model or request is not available. "
                "Check the Gemini model setting in Streamlit Secrets."
            )
        return "Gemini could not generate the report. Try again shortly."

    def _send(self, chat, message: str):
        last_exception: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return chat.send_message(
                    message,
                    request_options={"timeout": self.request_timeout},
                )
            except Exception as exc:
                last_exception = exc
                if not self._is_transient(exc) or attempt == self.MAX_RETRIES:
                    raise GeminiError(self._error_message(exc)) from exc
                time.sleep(min(2 ** attempt + random.uniform(0, 0.5), 8.0))
        raise GeminiError("Gemini could not generate the report.") from last_exception

    def generate_content(self, prompt: str) -> _GeneratedContent:
        chat = self._model.start_chat(history=[])
        text_parts: list[str] = []
        message = prompt
        for continuation in range(self.MAX_CONTINUATIONS + 1):
            response = self._send(chat, message)
            text = self._safe_text(response)
            finish_reason = self._finish_reason(response)
            if text:
                text_parts.append(text.rstrip())

            if finish_reason in {"STOP", "1"}:
                if not text_parts:
                    raise GeminiError(
                        "Gemini returned no report text. The response may have "
                        "been blocked; try again or choose another provider."
                    )
                return _GeneratedContent(text="\n\n".join(text_parts).strip())
            if finish_reason not in {"MAX_TOKENS", "2"}:
                raise GeminiError(
                    "Gemini stopped before completing the report "
                    f"({finish_reason or 'unknown reason'}). No partial report "
                    "was shown."
                )
            if continuation == self.MAX_CONTINUATIONS:
                raise GeminiError(
                    "Gemini could not complete this report within six responses. "
                    "No partial report was shown."
                )
            message = (
                "Continue from the exact point where the report stopped. Do not "
                "repeat prior content or restart the report. Complete every "
                "remaining required section."
            )

        raise GeminiError("Gemini could not complete the report.")

    def test_connection(self) -> str:
        """Run a minimal completion against Gemini."""
        response = self._send(
            self._model.start_chat(history=[]),
            "Reply with exactly: OK",
        )
        text = self._safe_text(response)
        if not text:
            raise GeminiError("Gemini returned no text for the connection test.")
        return text


class OpenRouterModel:
    """Expose OpenRouter through the Gemini-style interface used by reports."""

    MAX_CONTINUATIONS = 5
    MAX_RETRIES = 3

    def __init__(
        self,
        api_key: str,
        model_name: str,
        max_output_tokens: int | None = None,
        supports_reasoning: bool = True,
    ):
        self.api_key = api_key
        self.model_name = model_name
        self.max_output_tokens = min(
            max_output_tokens or OPENROUTER_REQUEST_OUTPUT_TOKENS,
            OPENROUTER_REQUEST_OUTPUT_TOKENS,
        )
        self.supports_reasoning = supports_reasoning
        self.fallback_from_unavailable_model = False

    def generate_content(self, prompt: str) -> _GeneratedContent:
        self.fallback_from_unavailable_model = False
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
                    "report text. Choose another free model.",
                    model_unavailable=True,
                )
            if continuation == self.MAX_CONTINUATIONS:
                raise OpenRouterError(
                    "The selected model could not complete this report within "
                    "six responses. No partial report was shown. Try the "
                    "automatic free router or a model with a larger output limit.",
                    model_unavailable=True,
                )
            final_attempt = continuation == self.MAX_CONTINUATIONS - 1
            messages.extend([
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "Continue from the exact point where the report stopped. "
                        "Do not repeat prior content, restart the report, or add "
                        "commentary. Complete every remaining required section."
                        + (
                            " Be concise and finish all remaining sections in "
                            "this response."
                            if final_attempt else ""
                        )
                    ),
                },
            ])

        raise OpenRouterError("OpenRouter could not complete the report.")

    @staticmethod
    def _retry_delay(response: requests.Response | None, attempt: int) -> float:
        retry_after = (
            response.headers.get("Retry-After")
            if response is not None else None
        )
        if retry_after:
            try:
                return min(max(float(retry_after), 0.5), 30.0)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    return min(max(seconds, 0.5), 30.0)
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(2 ** attempt + random.uniform(0, 0.5), 12.0)

    def _request(
        self,
        messages: list[dict[str, str]],
        model_name: str,
        max_tokens: int | None = None,
    ) -> requests.Response:
        request_body: dict[str, object] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens or self.max_output_tokens,
        }
        if self.supports_reasoning:
            request_body["reasoning"] = {
                "effort": "low",
                "exclude": True,
            }

        last_exception: requests.RequestException | None = None
        last_response: requests.Response | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = requests.post(
                    f"{OPENROUTER_API_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://semai-gsc-v4.streamlit.app/",
                        "X-OpenRouter-Title": "SEMAI Analytics Intelligence",
                    },
                    json=request_body,
                    timeout=(15, 240),
                )
                last_response = response
                if response.status_code not in _TRANSIENT_STATUS_CODES:
                    return response
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exception = exc
            except requests.RequestException as exc:
                raise OpenRouterError(
                    "OpenRouter could not be reached. Try again shortly.",
                    temporary=True,
                ) from exc

            if attempt < self.MAX_RETRIES:
                time.sleep(self._retry_delay(last_response, attempt))

        if last_response is not None:
            return last_response
        raise OpenRouterError(
            "OpenRouter could not be reached after several attempts. "
            "Try again shortly.",
            temporary=True,
        ) from last_exception

    def _generate_part(
        self,
        messages: list[dict[str, str]],
        allow_fallback: bool = True,
        max_tokens: int | None = None,
    ) -> tuple[str, str | None]:
        model_names = [self.model_name]
        if allow_fallback and self.model_name != OPENROUTER_FREE_ROUTER:
            model_names.append(OPENROUTER_FREE_ROUTER)

        response: requests.Response | None = None
        for index, model_name in enumerate(model_names):
            response = self._request(messages, model_name, max_tokens)
            if response.ok:
                break
            can_fallback = (
                index + 1 < len(model_names)
                and response.status_code in _TRANSIENT_STATUS_CODES | {404}
            )
            if can_fallback and response.status_code == 404:
                self.fallback_from_unavailable_model = True
            if not can_fallback:
                break

        if response is None:
            raise OpenRouterError("OpenRouter returned no response.")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            error_messages = {
                401: "OpenRouter rejected the server API key. Replace it in Streamlit Secrets.",
                402: "OpenRouter requires credits for this request or model.",
                404: "The selected OpenRouter model is no longer available.",
                429: (
                    "OpenRouter's free-tier request limit is currently exhausted, "
                    "even after automatic retries and fallback routing. Wait for "
                    "the limit to reset or add OpenRouter credits."
                ),
            }
            message = error_messages.get(
                response.status_code,
                f"OpenRouter request failed with status {response.status_code}.",
            )
            raise OpenRouterError(
                message,
                model_unavailable=response.status_code == 404,
                temporary=response.status_code in _TRANSIENT_STATUS_CODES,
            ) from exc
        try:
            payload = response.json()
            if payload.get("error"):
                raise KeyError("error response")
            choice = payload["choices"][0]
            message = choice["message"]
            text = message.get("content")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise OpenRouterError("OpenRouter returned an invalid response.") from exc
        if not text:
            if choice.get("error"):
                raise OpenRouterError(
                    "The selected OpenRouter model failed. Choose another free model.",
                    model_unavailable=True,
                )
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise OpenRouterError(
                    "The selected model used its output budget before producing "
                    "report text. Choose another free model.",
                    model_unavailable=True,
                )
            if finish_reason == "content_filter":
                raise OpenRouterError(
                    "The selected model filtered the response. Choose another free model.",
                    model_unavailable=True,
                )
            raise OpenRouterError(
                "The selected OpenRouter model returned no report text. "
                "Choose another free model.",
                model_unavailable=True,
            )
        return text, choice.get("finish_reason")

    def test_connection(self) -> str:
        """Run a minimal completion through the selected backend model."""
        messages = [{"role": "user", "content": "Reply with exactly: OK"}]
        text, _ = self._generate_part(
            messages,
            allow_fallback=False,
            max_tokens=16,
        )
        return text


def list_openrouter_free_models() -> list[dict[str, object]]:
    """Return free text models with enough capacity for full reports."""
    response = requests.get(f"{OPENROUTER_API_URL}/models", timeout=20)
    response.raise_for_status()
    models = []
    for model in response.json().get("data", []):
        pricing = model.get("pricing", {})
        architecture = model.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        output_modalities = architecture.get("output_modalities") or []
        supported_parameters = model.get("supported_parameters") or []
        top_provider = model.get("top_provider") or {}
        context_length = (
            top_provider.get("context_length") or model.get("context_length")
        )
        max_completion_tokens = top_provider.get("max_completion_tokens")
        if (
            str(pricing.get("prompt")) == "0"
            and str(pricing.get("completion")) == "0"
            and model.get("id")
            and "text" in input_modalities
            and "text" in output_modalities
            and "max_tokens" in supported_parameters
            and (context_length or 0) >= OPENROUTER_MIN_CONTEXT_TOKENS
            and (max_completion_tokens or 0)
            >= OPENROUTER_REQUEST_OUTPUT_TOKENS
        ):
            models.append({
                "id": model["id"],
                "name": model.get("name") or model["id"],
                "context_length": context_length,
                "max_completion_tokens": max_completion_tokens,
                "supports_reasoning": "reasoning" in supported_parameters,
            })
    return sorted(models, key=lambda item: item["name"].lower())