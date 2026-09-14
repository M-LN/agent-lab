"""Adding a model to config/models.yaml without disturbing the file.

The registry is hand-maintained and full of comments that explain why a model is
configured the way it is. Round-tripping it through a YAML dumper would flatten all
of that, so a new entry is inserted as text at the right place instead.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import CONFIG_DIR, load_config

HF_SECTION = "  # ------------------------------------------------- Hugging Face Inference Providers"
PARAM_RE = re.compile(r"[:\-](\d+(?:\.\d+)?)b\b", re.IGNORECASE)
# Models that spend part of their output budget reasoning before answering.
REASONING_HINTS = ("qwen3", "-r1", "deepseek-r1", "thinking", "reason", "qwythos", "gpt-oss")


def slugify(model: str) -> str:
    """ollama's 'qwen2.5-coder:7b' becomes 'qwen2.5-coder-7b'."""
    name = model.split("/")[-1]
    name = name.replace(":", "-").replace("_", "-")
    return re.sub(r"[^a-z0-9.\-]+", "-", name.lower()).strip("-")


def parameter_count(model: str) -> float | None:
    match = PARAM_RE.search(model)
    return float(match.group(1)) if match else None


def infer_tags(model: str, backend: str) -> list[str]:
    tags = ["local" if backend == "ollama" else "api"]
    params = parameter_count(model)
    if params is not None:
        tags.append("small" if params < 15 else "mid" if params < 40 else "large")
    if any(hint in model.lower() for hint in ("coder", "code")):
        tags.append("code")
    if any(hint in model.lower() for hint in REASONING_HINTS):
        tags.append("reasoning")
    return tags


def default_label(model: str, backend: str) -> str:
    base = model.split("/")[-1].split(":")[0].replace("-", " ").title()
    params = parameter_count(model)
    size = f" {params:g}B" if params else ""
    return f"{base}{size} ({'lokal' if backend == 'ollama' else 'HF'})"


def build_entry(
    model: str,
    *,
    backend: str = "ollama",
    model_id: str | None = None,
    label: str | None = None,
    token_budget: float | None = None,
) -> tuple[str, str]:
    """Return (model_id, YAML block) for a new registry entry."""
    prefix = "local" if backend == "ollama" else "hf"
    ident = model_id or f"{prefix}/{slugify(model)}"
    tags = infer_tags(model, backend)
    if token_budget is None and "reasoning" in tags:
        # Reasoning models need headroom or the probe measures budget fit, not capability.
        token_budget = 2.5

    lines = [
        f"  - id: {ident}",
        f"    label: {label or default_label(model, backend)}",
        f"    backend: {backend}",
        f"    model: {model}",
    ]
    # Without a parameter count the model is missing from any figure plotted
    # against scale, so infer it from the name when it is there to infer.
    params = parameter_count(model)
    if params is not None:
        lines.append(f"    parameters: {params:g}")
    lines.append(f"    tags: [{', '.join(tags)}]")
    if token_budget and token_budget > 1:
        lines.append(f"    token_budget: {token_budget:g}   # spends part of the budget reasoning")
    if "qwen3" in model.lower() and backend == "ollama":
        lines.append("    params:")
        lines.append("      think: false        # otherwise the answer is buried in chain-of-thought")
    return ident, "\n".join(lines) + "\n"


def existing_ids(path: Path | None = None) -> dict[str, str]:
    """model id -> model name, for everything already in the registry."""
    config = load_config(path) if path else load_config()
    return {m.id: m.model for m in config.models}


def add_entry(block: str, path: Path | None = None) -> Path:
    """Insert a block just before the hosted-models section, or append it."""
    target = path or CONFIG_DIR / "models.yaml"
    text = target.read_text(encoding="utf-8")
    if HF_SECTION in text:
        head, sep, tail = text.partition(HF_SECTION)
        text = f"{head.rstrip()}\n\n{block}\n{sep}{tail}"
    else:
        text = f"{text.rstrip()}\n\n{block}"
    target.write_text(text, encoding="utf-8", newline="\n")
    return target
