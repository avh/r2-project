"""Agent discovery and config/system-prompt resolution.

An "agent" is a leaf directory under `agents/` (e.g. `agents/user/r2`).
For each agent, configs and system prompts are resolved by walking from the
project root down through the agent path:

    project_root  ->  agents/  ->  agents/<tier>/  ->  ... ->  agents/<...>/<leaf>/

At each level a `config.json` (json5) and `system.txt` are optional. Configs
are dict-merged (deeper levels override shallower). The `models` list is the
exception: lists concatenate with dedup so each level can contribute models.
System prompts are concatenated with blank lines, top-down.
"""

from pathlib import Path

import json5

from .config import DEFAULT_CONFIG, normalize_model


# Prompt files looked up at each cascade level, in append order.
PROMPT_FILES = ("system.txt", "agent.txt", "user.txt")


def list_agents(agents_root):
    """Return agent IDs (POSIX-style relative paths) for leaf dirs under agents_root."""
    if not agents_root.exists() or not agents_root.is_dir():
        return []
    result = []

    def walk(d, parts):
        try:
            children = sorted(
                (p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.name,
            )
        except OSError:
            return
        if not children:
            if parts:  # don't list agents_root itself
                result.append("/".join(parts))
            return
        for c in children:
            walk(c, parts + [c.name])

    walk(agents_root, [])
    return result


def _cascade_dirs(project_root, agents_root, agent_id):
    yield project_root
    if not agents_root.exists():
        return
    yield agents_root
    if not agent_id:
        return
    current = agents_root
    for part in Path(agent_id).parts:
        current = current / part
        yield current


def _merge_config(base, overlay):
    for key, val in overlay.items():
        if key == "models" and isinstance(val, list):
            merged = list(base.get("models", []))
            for m in val:
                if m not in merged:
                    merged.append(m)
            base[key] = merged
        else:
            base[key] = val
    return base


def resolve_agent(project_root, agents_root, agent_id):
    """Return {'config': dict, 'system_prompt': str} for an agent ID."""
    config = dict(DEFAULT_CONFIG)
    system_parts = []
    for d in _cascade_dirs(project_root, agents_root, agent_id):
        cfg_path = d / "config.json"
        if cfg_path.exists():
            try:
                overlay = json5.loads(cfg_path.read_text())
                _merge_config(config, overlay)
            except (ValueError, OSError):
                pass
        for name in PROMPT_FILES:
            prompt_path = d / name
            if not prompt_path.exists():
                continue
            try:
                text = prompt_path.read_text().strip()
            except OSError:
                continue
            if text:
                system_parts.append(text)
    return {"config": config, "system_prompt": "\n\n".join(system_parts)}


def agent_info(project_root, agents_root, agent_id):
    """Summary for /api/agents: id + resolved default_model + models list."""
    resolved = resolve_agent(project_root, agents_root, agent_id)
    cfg = resolved["config"]
    default_model = cfg.get("default_model") or ""
    return {
        "id": agent_id,
        "default_model": normalize_model(default_model) if default_model else "",
        "default_thinking": bool(cfg.get("default_thinking", False)),
        "models": [normalize_model(m) for m in cfg.get("models", [])],
    }
