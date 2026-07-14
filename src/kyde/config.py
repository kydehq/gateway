"""
Gateway configuration loader.

Reads an optional YAML config file (default: config.yaml in the project root,
overridable via the KYDE_CONFIG env var) and merges it with the built-in
upstream defaults.

Config format:
  upstreams:
    <name>:
      base: <url>           # required
      api_prefix: <path>    # optional, defaults to ""

Any upstream name that matches a built-in entry overrides it; new names extend
the registry and become valid path prefixes automatically.

Example config.yaml:
  upstreams:
    openai:
      base: http://my-openai-proxy.internal
      api_prefix: /v1
    ollama:
      base: http://localhost:11434
      api_prefix: /v1
    vllm:
      base: http://localhost:8000
      api_prefix: /v1
"""

import os
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Built-in defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, dict] = {
    "openai": {
        "base": "https://api.openai.com",
        "api_prefix": "/v1",
    },
    "anthropic": {
        "base": "https://api.anthropic.com",
        "api_prefix": "/v1",
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_prefix": "",
    },
    "copilot": {
        "base": "https://api.githubcopilot.com",
        "api_prefix": "",
    },
}

# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _config_path() -> Path | None:
    """Return the config file path from env or the default location.

    Resolution order:
      1. KYDE_CONFIG env var (absolute or relative path)
      2. config.yaml in the current working directory (cwd is /app in Docker,
         the project root in development)
    """
    env = os.getenv("KYDE_CONFIG", "").strip()
    if env:
        return Path(env)
    return Path.cwd() / "config.yaml"


def load_upstreams() -> dict[str, dict]:
    """
    Return the upstream registry, merging built-in defaults with any
    user-supplied config file.  Safe to call at module import time.
    """
    upstreams = {k: dict(v) for k, v in _DEFAULTS.items()}

    path = _config_path()
    if path is None:
        print("  ℹ config: no config file path resolved, using defaults")
        return upstreams
    if not path.exists():
        print(f"  ℹ config: {path} not found, using defaults")
        return upstreams
    print(f"  ℹ config: loading {path}")

    if yaml is None:
        print(f"  ⚠ config: PyYAML not installed — skipping {path}")
        return upstreams

    try:
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:
        print(f"  ⚠ config: failed to read {path}: {exc}")
        return upstreams

    raw = data.get("upstreams")
    if not isinstance(raw, dict):
        return upstreams

    for name, entry in raw.items():
        if not isinstance(entry, dict) or "base" not in entry:
            print(f"  ⚠ config: upstream {name!r} missing 'base', skipping")
            continue
        raw_prefix = entry.get("api_prefix") or ""
        upstreams[name] = {
            "base": str(entry["base"]).rstrip("/"),
            "api_prefix": str(raw_prefix),
        }
        print(f"  ✓ config: upstream {name!r} → {upstreams[name]['base']}")

    return upstreams
