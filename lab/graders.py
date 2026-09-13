"""Deterministic graders. Every check returns (passed, detail)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import jsonschema

CheckResult = tuple[bool, str]
GRADERS: dict[str, Callable[..., CheckResult]] = {}

NUMBER_RE = re.compile(r"-?\d[\d.,]*")
US_GROUPED = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
EU_GROUPED = re.compile(r"^-?\d{1,3}(?:\.\d{3})+(?:,\d+)?$")
TRIM_CHARS = ".!?\"' `"
DANISH_MARKERS = {
    "og", "ikke", "til", "med", "det", "den", "der", "som", "for", "har",
    "kan", "skal", "være", "af", "på", "en", "et", "at", "vi", "du",
    "blev", "bliver", "efter", "havde", "var", "fra", "ved", "men", "eller",
    "når", "hvor", "under", "over", "mellem", "sig", "man", "sin", "så", "de",
}
# Words that are frequent in English but not Danish - they are the strongest
# signal that a model ignored a "answer in Danish" instruction.
ENGLISH_MARKERS = {
    "the", "and", "is", "are", "was", "were", "of", "to", "in", "that",
    "with", "this", "it", "you", "we", "on", "as", "be", "not", "have",
}


def grader(name: str) -> Callable:
    def wrap(fn: Callable[..., CheckResult]) -> Callable[..., CheckResult]:
        GRADERS[name] = fn
        return fn

    return wrap


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _strip_fences(text: str) -> str:
    """Take the JSON out of fenced code blocks or surrounding prose."""
    fenced = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
    candidate = (fenced.group(1) if fenced else text).strip()
    if candidate.startswith(("{", "[")):
        return candidate
    starts = [i for i in (candidate.find("{"), candidate.find("[")) if i != -1]
    if not starts:
        return candidate
    start = min(starts)
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    return candidate[start : end + 1] if end > start else candidate


def parse_json(text: str) -> tuple[Any | None, str]:
    try:
        return json.loads(_strip_fences(text)), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:120]


# --------------------------------------------------------------------------- text


@grader("contains")
def check_contains(output: str, *, value: str | list[str], mode: str = "all", **_: Any) -> CheckResult:
    values = [value] if isinstance(value, str) else list(value)
    text = _norm(output)
    hits = [v for v in values if _norm(v) in text]
    if mode == "any":
        return bool(hits), f"{len(hits)}/{len(values)} found (any)"
    missing = [v for v in values if v not in hits]
    return not missing, "all found" if not missing else f"missing: {missing}"


@grader("not_contains")
def check_not_contains(output: str, *, value: str | list[str], **_: Any) -> CheckResult:
    values = [value] if isinstance(value, str) else list(value)
    text = _norm(output)
    leaked = [v for v in values if _norm(v) in text]
    return not leaked, "clean" if not leaked else f"leaked: {leaked}"


@grader("regex")
def check_regex(output: str, *, pattern: str, should_match: bool = True, **_: Any) -> CheckResult:
    found = re.search(pattern, output, re.IGNORECASE | re.MULTILINE | re.DOTALL) is not None
    return found == should_match, f"match={found} expected={should_match}"


@grader("equals")
def check_equals(output: str, *, value: str, strip_punct: bool = True, **_: Any) -> CheckResult:
    got, want = _norm(output), _norm(value)
    if strip_punct:
        got, want = got.strip(TRIM_CHARS), want.strip(TRIM_CHARS)
    return got == want, f"got={got[:60]!r} want={want[:60]!r}"


def extract_numbers(text: str) -> list[float]:
    """Pull numbers out of prose, tolerating 15,795 / 15.795 / 15795 thousand separators."""
    numbers: list[float] = []
    for token in NUMBER_RE.findall(text):
        token = token.rstrip(".,")
        if not token or not re.search(r"\d", token):
            continue
        if US_GROUPED.match(token):
            cleaned = token.replace(",", "")
        elif EU_GROUPED.match(token):
            cleaned = token.replace(".", "").replace(",", ".")
        else:
            cleaned = token.replace(",", ".")
        try:
            numbers.append(float(cleaned))
        except ValueError:
            continue
    return numbers


@grader("numeric")
def check_numeric(output: str, *, value: float, tol: float = 0.0, mode: str = "any", **_: Any) -> CheckResult:
    numbers = extract_numbers(output)
    if not numbers:
        return False, "no number in output"
    if mode == "last":
        candidates = numbers[-1:]
    elif mode == "first":
        candidates = numbers[:1]
    else:
        candidates = numbers
    hit = any(abs(n - value) <= tol for n in candidates)
    return hit, f"want={value}+-{tol} saw={candidates[:8]}"


@grader("word_count")
def check_word_count(output: str, *, max: int | None = None, min: int | None = None, **_: Any) -> CheckResult:
    words = len(re.findall(r"\S+", output))
    if max is not None and words > max:
        return False, f"{words} words > max {max}"
    if min is not None and words < min:
        return False, f"{words} words < min {min}"
    return True, f"{words} words"


@grader("line_count")
def check_line_count(output: str, *, value: int | None = None, pattern: str | None = None, **_: Any) -> CheckResult:
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if pattern:
        lines = [ln for ln in lines if re.match(pattern, ln.strip())]
    ok = value is None or len(lines) == value
    return ok, f"{len(lines)} matching line(s), want {value}"


@grader("no_markdown")
def check_no_markdown(output: str, **_: Any) -> CheckResult:
    markers = [m for m in ("```", "**", "##", "* ", "- ") if m in output]
    return not markers, "plain" if not markers else f"markdown markers: {markers}"


@grader("language")
def check_language(output: str, *, lang: str = "da", min_hits: int = 2, **_: Any) -> CheckResult:
    """Danish detection that also works on a single short sentence.

    A short translation can easily contain zero stopwords from a fixed list, so
    English stopwords count as evidence against, and æ/ø/å as evidence for.
    """
    if lang != "da":
        return True, "only da supported, skipped"
    words = set(re.findall(r"[a-zæøå]+", output.lower()))
    da_hits = words & DANISH_MARKERS
    en_hits = words & ENGLISH_MARKERS
    has_special = bool(re.search(r"[æøåÆØÅ]", output))

    if not output.strip():
        return False, "empty output"
    if len(en_hits) > len(da_hits):
        ok = False
    else:
        ok = len(da_hits) >= min_hits or has_special
    return ok, f"da={len(da_hits)} en={len(en_hits)} special_chars={has_special}"


# --------------------------------------------------------------------------- structure


@grader("json_valid")
def check_json_valid(output: str, **_: Any) -> CheckResult:
    data, err = parse_json(output)
    return data is not None, "valid JSON" if data is not None else f"invalid: {err}"


@grader("json_schema")
def check_json_schema(output: str, *, schema: dict, **_: Any) -> CheckResult:
    data, err = parse_json(output)
    if data is None:
        return False, f"invalid JSON: {err}"
    try:
        jsonschema.validate(data, schema)
        return True, "schema ok"
    except jsonschema.ValidationError as exc:
        return False, f"schema: {exc.message[:120]}"


@grader("json_path")
def check_json_path(output: str, *, path: str, value: Any = None, exists: bool = True, **_: Any) -> CheckResult:
    """Dotted path lookup, e.g. items.0.name"""
    data, err = parse_json(output)
    if data is None:
        return False, f"invalid JSON: {err}"
    node: Any = data
    for part in path.split("."):
        try:
            node = node[int(part)] if part.isdigit() else node[part]
        except Exception:  # noqa: BLE001
            return (not exists), f"path '{path}' missing"
    if value is None:
        return exists, f"path ok -> {str(node)[:60]}"
    match = _norm(str(node)) == _norm(str(value))
    return match, f"{path}={node!r} want {value!r}"


@grader("strict_json_only")
def check_strict_json_only(output: str, **_: Any) -> CheckResult:
    """Output must be raw JSON: no fences, no prose before or after."""
    stripped = output.strip()
    if not stripped.startswith(("{", "[")):
        return False, f"starts with {stripped[:20]!r}"
    try:
        json.loads(stripped)
        return True, "raw JSON, no wrapper"
    except Exception as exc:  # noqa: BLE001
        return False, f"not parseable as-is: {str(exc)[:80]}"


# --------------------------------------------------------------------------- code


@grader("code_exec")
def check_code_exec(
    output: str,
    *,
    tests: str,
    timeout: int = 15,
    allow_code_exec: bool = False,
    **_: Any,
) -> CheckResult:
    """Run the model's Python in a subprocess and assert against tests.

    Disabled unless the run is started with --allow-code-exec.
    """
    if not allow_code_exec:
        return False, "skipped (code execution disabled)"

    fenced = re.findall(r"```(?:python|py)?\s*(.*?)```", output, re.DOTALL)
    code = fenced[0] if fenced else output
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "candidate.py"
        script.write_text(f"{code}\n\n{tests}\n", encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return False, f"timeout after {timeout}s"
    if proc.returncode == 0:
        return True, "tests passed"
    return False, (proc.stderr.strip().splitlines() or ["non-zero exit"])[-1][:160]


# --------------------------------------------------------------------------- runner


def run_checks(output: str, checks: list[dict], *, allow_code_exec: bool = False) -> tuple[float, list[dict]]:
    """Weighted pass rate over one prompt's checks."""
    details: list[dict] = []
    total_weight = 0.0
    earned = 0.0

    for raw_spec in checks:
        spec = dict(raw_spec)
        kind = spec.pop("type")
        weight = float(spec.pop("weight", 1.0))
        fn = GRADERS.get(kind)
        total_weight += weight
        if fn is None:
            details.append({"type": kind, "passed": False, "weight": weight, "detail": "unknown check type"})
            continue
        try:
            passed, detail = fn(output, allow_code_exec=allow_code_exec, **spec)
        except TypeError as exc:
            passed, detail = False, f"bad check args: {exc}"
        earned += weight if passed else 0.0
        details.append({"type": kind, "passed": passed, "weight": weight, "detail": detail})

    score = earned / total_weight if total_weight else 0.0
    return round(score, 4), details
