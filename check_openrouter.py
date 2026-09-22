"""Check OpenRouter authentication and completion health without exposing secrets."""

from __future__ import annotations

import json
import sys

import requests

from config import OPENROUTER_API_KEY
from services.llm import OPENROUTER_API_URL, OPENROUTER_FREE_ROUTER


def main() -> int:
    if not OPENROUTER_API_KEY:
        print("FAIL: OPENROUTER_API_KEY is not configured in this environment.")
        return 1

    try:
        response = requests.post(
            f"{OPENROUTER_API_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://semai-gsc-v4.streamlit.app/",
                "X-OpenRouter-Title": "SEMAI Analytics Intelligence Check",
            },
            json={
                "model": OPENROUTER_FREE_ROUTER,
                "messages": [
                    {"role": "user", "content": "Reply with exactly: OK"},
                ],
                "temperature": 0,
                "max_tokens": 256,
                "reasoning": {
                    "effort": "minimal",
                    "exclude": True,
                },
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        print(f"FAIL: network error ({exc.__class__.__name__}).")
        return 1

    print(f"HTTP status: {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        print("FAIL: OpenRouter returned a non-JSON response.")
        return 1

    if not response.ok:
        error = payload.get("error", {})
        print(f"FAIL: API error code={error.get('code', response.status_code)}")
        return 1

    choices = payload.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    content = message.get("content")
    usage = payload.get("usage") or {}
    diagnostics = {
        "resolved_model": payload.get("model"),
        "finish_reason": choice.get("finish_reason"),
        "has_choice_error": bool(choice.get("error")),
        "content_length": len(content) if isinstance(content, str) else 0,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": (
            usage.get("completion_tokens_details") or {}
        ).get("reasoning_tokens"),
    }
    print(json.dumps(diagnostics, indent=2))
    if diagnostics["content_length"] == 0:
        print("FAIL: authenticated request completed without visible text.")
        return 2

    print("PASS: OpenRouter authentication and text completion are working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())