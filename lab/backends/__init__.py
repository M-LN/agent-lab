from .base import Backend, Completion
from .hf import HFBackend
from .ollama import OllamaBackend

REGISTRY: dict[str, type[Backend]] = {
    "ollama": OllamaBackend,
    "hf": HFBackend,
}


def get_backend(name: str, **options) -> Backend:
    try:
        return REGISTRY[name](**options)
    except KeyError:
        raise ValueError(f"unknown backend '{name}' (known: {', '.join(REGISTRY)})") from None


__all__ = ["Backend", "Completion", "HFBackend", "OllamaBackend", "REGISTRY", "get_backend"]
