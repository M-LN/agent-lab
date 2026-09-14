"""Adding a model edits a hand-maintained file, so the edit gets tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.registry import HF_SECTION, add_entry, build_entry, infer_tags, parameter_count, slugify


def test_slug_and_parameter_count():
    assert slugify("qwen2.5-coder:7b") == "qwen2.5-coder-7b"
    assert slugify("hf.co/empero-ai/Some-Model:Q4_K_M") == "some-model-q4-k-m"
    assert parameter_count("llama3.1:8b") == 8.0
    assert parameter_count("Qwen/Qwen2.5-72B-Instruct") == 72.0
    assert parameter_count("mistral:latest") is None


def test_tags_follow_the_model_name():
    assert "small" in infer_tags("llama3.1:8b", "ollama")
    assert "large" in infer_tags("Qwen/Qwen2.5-72B-Instruct", "hf")
    assert "code" in infer_tags("qwen2.5-coder:7b", "ollama")
    assert "reasoning" in infer_tags("qwen3:8b", "ollama")
    assert infer_tags("gemma3:12b", "ollama")[0] == "local"


def test_reasoning_models_get_budget_headroom():
    _, block = build_entry("qwen3:14b")
    assert "token_budget" in block, "a reasoning model without headroom measures budget fit"
    _, plain = build_entry("gemma3:12b")
    assert "token_budget" not in plain


def test_qwen3_gets_thinking_disabled():
    _, block = build_entry("qwen3:14b")
    assert "think: false" in block


def test_entry_shape():
    model_id, block = build_entry("gemma3:12b")
    assert model_id == "local/gemma3-12b"
    assert "  - id: local/gemma3-12b" in block
    assert "    backend: ollama" in block
    assert "    model: gemma3:12b" in block
    assert block.endswith("\n")


def test_hf_entry():
    model_id, block = build_entry("google/gemma-3-27b-it", backend="hf")
    assert model_id == "hf/gemma-3-27b-it"
    assert "backend: hf" in block
    assert "(HF)" in block


def test_entry_is_inserted_before_the_hosted_section(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        "models:\n  - id: local/a\n    model: a\n\n" + HF_SECTION + "\n  - id: hf/b\n    model: b\n",
        encoding="utf-8",
    )
    _, block = build_entry("gemma3:12b")
    add_entry(block, path)

    text = path.read_text(encoding="utf-8")
    assert text.index("local/gemma3-12b") < text.index(HF_SECTION), "new local models go with the local ones"
    assert "hf/b" in text and "local/a" in text, "existing entries survive"


def test_comments_survive_the_edit(tmp_path):
    path = tmp_path / "models.yaml"
    original = (
        "# registry header comment\nmodels:\n  - id: local/a\n    model: a\n"
        "    params:\n      think: false        # keep this note\n\n" + HF_SECTION + "\n  - id: hf/b\n    model: b\n"
    )
    path.write_text(original, encoding="utf-8")
    _, block = build_entry("gemma3:12b")
    add_entry(block, path)

    text = path.read_text(encoding="utf-8")
    assert "# registry header comment" in text
    assert "# keep this note" in text


def test_entry_carries_the_parameter_count():
    """Without it the model is missing from every figure plotted against scale."""
    _, block = build_entry("llama3.1:8b")
    assert "    parameters: 8\n" in block

    _, gguf = build_entry("hf.co/mlabonne/Meta-Llama-3.1-8B-Instruct-abliterated-GGUF:Q4_K_M")
    assert "    parameters: 8\n" in gguf

    _, unknown = build_entry("mistral:latest")
    assert "parameters:" not in unknown, "a count that cannot be read must not be invented"
