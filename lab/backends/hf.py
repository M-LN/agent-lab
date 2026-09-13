"""Hugging Face Inference Providers (OpenAI-compatible router)."""
from __future__ import annotations

import os
from typing import Any

import requests

from .base import Backend, Completion

DEFAULT_ROUTER = "https://router.huggingface.co/v1"


class HFBackend(Backend):
    name = "hf"

    def __init__(self, base_url: str | None = None, token: str | None = None, **options: Any) -> None:
        super().__init__(**options)
        self.base_url = (base_url or os.getenv("HF_BASE_URL") or DEFAULT_ROUTER).rstrip("/")
        self.token = token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")

    def available(self) -> tuple[bool, str]:
        if not self.token:
            return False, "no HF_TOKEN in environment or .env"
        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            if response.status_code == 401:
                return False, "HF_TOKEN rejected (401)"
            response.raise_for_status()
            return True, "router reachable"
        except Exception as exc:  # noqa: BLE001
            return False, f"unreachable: {exc}"

    def _call(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: int,
        extra: dict[str, Any],
    ) -> Completion:
        if not self.token:
            raise RuntimeError("HF_TOKEN missing - add it to .env")

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if extra.get("response_format"):
            payload["response_format"] = extra["response_format"]
        if extra.get("provider"):
            # Route to one specific inference provider instead of auto-selection.
            payload["model"] = f"{model}:{extra['provider']}"

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")

        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        return Completion(
            text=message.get("content") or "",
            reasoning=message.get("reasoning_content"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            truncated=choice.get("finish_reason") == "length",
            raw={"finish_reason": choice.get("finish_reason"), "provider": data.get("provider")},
        )
