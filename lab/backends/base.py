"""Backend interface shared by every model provider."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


@dataclass
class Completion:
    """One model answer plus everything we want to score or chart later."""

    text: str = ""
    latency_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    reasoning: str | None = None
    truncated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


def split_reasoning(text: str) -> tuple[str, str | None]:
    """Pull <think>...</think> out of the answer so graders see the real output."""
    blocks = re.findall(r"<think>(.*?)</think>", text, re.DOTALL | re.IGNORECASE)
    if not blocks:
        return text.strip(), None
    return THINK_BLOCK.sub("", text).strip(), "\n".join(b.strip() for b in blocks)


class Backend:
    """Base class. Subclasses implement _call()."""

    name = "base"

    def __init__(self, **options: Any) -> None:
        self.options = options

    def available(self) -> tuple[bool, str]:
        """Cheap reachability/credential probe."""
        return True, "ok"

    def complete(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 800,
        timeout: int = 120,
        retries: int = 2,
        extra: dict[str, Any] | None = None,
    ) -> Completion:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_error = "unknown error"
        for attempt in range(retries + 1):
            started = time.perf_counter()
            try:
                completion = self._call(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    extra=extra or {},
                )
                completion.latency_s = round(time.perf_counter() - started, 3)
                # Keep reasoning the backend already reported; only fill it from an
                # inline <think> block when there is one.
                completion.text, inline_reasoning = split_reasoning(completion.text)
                if inline_reasoning:
                    completion.reasoning = inline_reasoning
                return completion
            except Exception as exc:  # noqa: BLE001 - every backend failure is a datapoint
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))

        return Completion(error=last_error, latency_s=0.0)

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
        raise NotImplementedError
