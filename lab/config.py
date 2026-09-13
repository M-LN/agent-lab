"""Loading of the model registry and the prompt suites."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SUITES_DIR = ROOT / "suites"
RESULTS_DIR = ROOT / "results"


def load_env() -> None:
    load_dotenv(ROOT / ".env")


@dataclass
class ModelSpec:
    id: str
    backend: str
    model: str
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    label: str | None = None
    # Multiplier on each prompt's max_tokens. Reasoning models spend part of the
    # budget thinking, so a fixed cap would measure budget fit, not capability.
    token_budget: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.label or self.id


@dataclass
class PromptSpec:
    id: str
    prompt: str
    checks: list[dict]
    category: str = "general"
    system: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    weight: float = 1.0
    note: str | None = None
    # A ladder is one topic in rungs of rising sensitivity; the pattern is which
    # rung a model stops at, not whether any single answer passes.
    ladder: str | None = None
    rung: int | None = None
    # An optional prior exchange, so a prompt can test what a model does under
    # pushback rather than only what it says first.
    messages: list[dict[str, str]] | None = None

    @property
    def fingerprint(self) -> str:
        """Full identity: changing a check changes the fingerprint."""
        payload = json.dumps(
            {"p": self.prompt, "s": self.system, "c": self.checks, "h": self.messages},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    @property
    def io_hash(self) -> str:
        """Identity of what was actually sent to the model.

        Unchanged when only the checks are edited, which is what makes a stored
        answer safe to re-grade instead of re-running.
        """
        payload = json.dumps(
            {
                "p": self.prompt,
                "s": self.system,
                "m": self.max_tokens,
                "t": self.temperature,
                "h": self.messages,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class Suite:
    name: str
    description: str
    prompts: list[PromptSpec]
    # A measurement suite records behaviour without grading it, so it never
    # enters the leaderboard where a "score" would be meaningless.
    scored: bool = True


@dataclass
class LabConfig:
    defaults: dict[str, Any]
    models: list[ModelSpec]
    backend_options: dict[str, dict[str, Any]]
    concurrency: dict[str, int]

    def select(self, patterns: list[str] | None, include_disabled: bool = False) -> list[ModelSpec]:
        pool = [m for m in self.models if m.enabled or include_disabled]
        if not patterns:
            return pool
        chosen: list[ModelSpec] = []
        for model in pool:
            haystack = [model.id, model.backend, model.model, *model.tags]
            if any(fnmatch.fnmatch(h, p) for p in patterns for h in haystack):
                chosen.append(model)
        return chosen


def load_config(path: Path | None = None) -> LabConfig:
    load_env()
    data = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    models = [ModelSpec(**m) for m in data.get("models", [])]
    return LabConfig(
        defaults=data.get("defaults", {}),
        models=models,
        backend_options=data.get("backends", {}),
        concurrency=data.get("concurrency", {}),
    )


def load_suite(name_or_path: str) -> Suite:
    path = Path(name_or_path)
    if not path.exists():
        path = SUITES_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"suite not found: {name_or_path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    prompts = [PromptSpec(**p) for p in data.get("prompts", [])]
    return Suite(
        name=data.get("suite", path.stem),
        description=data.get("description", ""),
        prompts=prompts,
        scored=bool(data.get("scored", True)),
    )


def available_suites() -> list[str]:
    return sorted(p.stem for p in SUITES_DIR.glob("*.yaml"))


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
