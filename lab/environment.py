"""What the local models actually ran on.

A median latency means nothing without it. These numbers come from the machine
rather than from prose, and are recorded into each run's meta.json, so a run
always carries the conditions that produced its timings.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from typing import Any


def _run(args: list[str], timeout: int = 15) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - absence of a tool is an answer, not an error
        return ""


def gpus() -> list[dict[str, Any]]:
    raw = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    found = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            found.append({"name": parts[0], "memory": parts[1], "driver": parts[2]})
    return found


def system_memory_gb() -> float | None:
    if platform.system() == "Windows":
        raw = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
        if raw.isdigit():
            return round(int(raw) / 1024**3, 1)
    raw = _run(["free", "-b"])
    for line in raw.splitlines():
        if line.lower().startswith("mem:"):
            return round(int(line.split()[1]) / 1024**3, 1)
    return None


def cpu_name() -> str:
    if platform.system() == "Windows":
        name = _run(["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"])
        if name:
            return " ".join(name.split())
    return platform.processor() or platform.machine()


def ollama_version() -> str | None:
    if not shutil.which("ollama"):
        return None
    raw = _run(["ollama", "--version"])
    return raw.split()[-1] if raw else None


def local_models(names: list[str]) -> dict[str, dict[str, str]]:
    """Quantisation and context length per pulled model, straight from ollama."""
    details: dict[str, dict[str, str]] = {}
    for name in names:
        raw = _run(["ollama", "show", name])
        if not raw:
            continue
        fields: dict[str, str] = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in {"quantization", "parameters"}:
                fields[parts[0]] = parts[1]
            elif len(parts) >= 3 and parts[0] == "context" and parts[1] == "length":
                fields["context"] = parts[2]
        if fields:
            details[name] = fields
    return details


def capture(model_names: list[str] | None = None) -> dict[str, Any]:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu_name(),
        "cores": f"{_run(['powershell', '-NoProfile', '-Command', '(Get-CimInstance Win32_Processor | Select-Object -First 1).NumberOfCores'])}C"
        if platform.system() == "Windows"
        else None,
        "memory_gb": system_memory_gb(),
        "gpus": gpus(),
        "ollama": ollama_version(),
        "python": platform.python_version(),
        "models": local_models(model_names or []),
    }


if __name__ == "__main__":
    print(json.dumps(capture(), indent=2))
