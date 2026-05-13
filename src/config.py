"""Configuration loading and model-name parsing."""

import sys

import json5


OLLAMA_PREFIX = "ollama:"
OLLAMA_CLOUD_PREFIX = "ollama-cloud:"
OPENAI_PREFIX = "openai:"
OMLX_PREFIX = "omlx:"
OMLX_ORG = "omlx/"

DEFAULT_CONFIG = {
    "port": 8080,
    "default_model": "ollama:qwen2.5-coder:7b",
    "default_thinking": False,
    "mlx_url": "http://localhost:8081/v1/chat/completions",
    "models": [
        "ollama:qwen2.5-coder:7b",
        "ollama:llama3.1:8b",
        "ollama-cloud:gpt-oss:120b",
        "ollama-cloud:llama3.3:70b",
        "openai:gpt-4o-mini",
        "openai:gpt-4o",
        "openai:gpt-4.1-mini",
        "omlx:Qwen3.6-35B-A3B-UD-MLX-4bit",
    ],
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if not path.exists():
        return cfg
    try:
        cfg.update(json5.loads(path.read_text()))
    except (ValueError, OSError) as e:
        sys.stderr.write(f"[config] failed to read {path}: {e}; using defaults\n")
    return cfg


def parse_model(model):
    """Return (provider, api_model). Bare names default to 'ollama' (local)."""
    if model.startswith(OPENAI_PREFIX):
        return "openai", model[len(OPENAI_PREFIX):]
    if model.startswith(OLLAMA_CLOUD_PREFIX):
        return "ollama-cloud", model[len(OLLAMA_CLOUD_PREFIX):]
    if model.startswith(OMLX_PREFIX):
        return "omlx", model[len(OMLX_PREFIX):]
    if model.startswith(OLLAMA_PREFIX):
        return "ollama", model[len(OLLAMA_PREFIX):]
    return "ollama", model


def normalize_model(model):
    provider, name = parse_model(model)
    return f"{provider}:{name}"


def extract_thinking(delta):
    """Pull reasoning text out of an OpenAI-compatible streaming delta.

    Different providers emit reasoning under different keys (`reasoning_content`,
    `reasoning`, `reasoning_details`) and in different shapes (string, list of
    typed parts, object with `content`/`text`). Returns a string or "".
    """
    for key in ("reasoning_content", "reasoning", "reasoning_details"):
        if key not in delta:
            continue
        val = delta[key]
        if isinstance(val, str):
            if val:
                return val
        elif isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("text") or item.get("content") or "")
            joined = "".join(parts)
            if joined:
                return joined
        elif isinstance(val, dict):
            text = val.get("content") or val.get("text") or ""
            if text:
                return text
    return ""
