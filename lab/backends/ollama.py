"""Local models through the Ollama daemon (http://localhost:11434)."""
from __future__ import annotations

import os
from typing import Any

import requests

from .base import Backend, Completion


class OllamaBackend(Backend):
    name = "ollama"

    def __init__(self, host: str | None = None, **options: Any) -> None:
        super().__init__(**options)
        self.host = (host or os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")

    def available(self, model: str | None = None) -> tuple[bool, str]:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            response.raise_for_status()
            names = [m["name"] for m in response.json().get("models", [])]
            if model and model not in names:
                return False, f"model not pulled: {model} (ollama pull {model})"
            return True, f"{len(names)} model(s) pulled"
        except Exception as exc:  # noqa: BLE001
            return False, f"unreachable: {exc}"

    def installed_models(self) -> list[str]:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except Exception:  # noqa: BLE001
            return []

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "seed": extra.get("seed", 42),
            },
        }
        # qwen3 and friends emit chain-of-thought unless thinking is turned off.
        if "think" in extra:
            payload["think"] = extra["think"]
        if extra.get("format"):
            payload["format"] = extra["format"]

        response = requests.post(f"{self.host}/api/chat", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        text = message.get("content", "") or ""
        thinking = message.get("thinking")
        return Completion(
            text=text,
            reasoning=thinking,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            truncated=data.get("done_reason") == "length",
            raw={"done_reason": data.get("done_reason")},
        )
