# Yak Bot

A small browser-based chat bot that talks to local Ollama, hosted Ollama Cloud,
OpenAI, or a local MLX server from one UI. Single-process Python server, plain
HTML/JS frontend, no build step.

## Requirements

- Python 3.9+
- `pip install requests json5`
- A backing model provider:
  - **Local Ollama** running at `http://localhost:11434`
  - **Ollama Cloud** — set `OLLAMA_API_KEY`
  - **OpenAI** — set `OPENAI_API_KEY`
  - **MLX** — a running [mlx-lm](https://github.com/ml-explore/mlx-lm) server
    (default URL `http://localhost:8081/v1/chat/completions`). Set
    `MLX_API_KEY` if the endpoint requires bearer auth.

## Running

```
./server.sh [model]
```

The wrapper `cd`s to the project root, sources `server.env` if present, and
runs `python3 -m src.server`, so it works from any directory. `model` overrides
the `default_model` from `config.json`. Open http://localhost:8080.

Put long-lived API keys in `server.env` (one `KEY=VALUE` per line, no quotes
needed) — `server.sh` exports them before launching the server:

```
# server.env
OLLAMA_API_KEY=...
OPENAI_API_KEY=...
MLX_API_KEY=...
```

Examples:

```
./server.sh ollama:qwen2.5-coder:7b
./server.sh ollama-cloud:gpt-oss:120b      # uses OLLAMA_API_KEY from server.env
./server.sh openai:gpt-4o-mini             # uses OPENAI_API_KEY from server.env
./server.sh omlx:Qwen3.6-35B-A3B-UD-MLX-4bit
```

For the MLX provider, start mlx-lm separately on a non-conflicting port:

```
pip install mlx-lm
mlx_lm.server --model omlx/Qwen3.6-35B-A3B-UD-MLX-4bit --port 8081
```

## Model names

Names are prefixed by provider:

- `ollama:<name>` — local Ollama
- `ollama-cloud:<name>` — Ollama Cloud
- `openai:<name>` — OpenAI Chat Completions API
- `omlx:<name>` — local mlx-lm server (OpenAI-compatible); `omlx/` org prefix is implicit

A bare name is treated as `ollama:`.

## Agents

Sessions are started by selecting an **agent**. An agent is a leaf directory
under `agents/` (a directory with no further subdirectories). Its ID is the
path relative to `agents/`, e.g. `user/r2` lives at `agents/user/r2/`.

At every level of the path you can drop in a `config.json` and/or `system.txt`;
the agent's effective config and system prompt are built by walking from the
project root down through the agent's tiers:

```
project_root/config.json + system.txt
└── agents/config.json + system.txt
    └── agents/<tier>/config.json + system.txt
        └── agents/<...>/<leaf>/config.json + system.txt
```

- **Config merge** — deeper levels override shallower ones (`dict.update`
  semantics). The exception is `models`: lists concatenate with dedup so each
  level can add to the available models.
- **System prompt** — at each level, non-empty `system.txt`, `agent.txt`, and
  `user.txt` files (in that order) are appended to the final system prompt,
  joined by a blank line. All three are conceptually system content; the split
  is just for organization (general instructions / agent persona / user info).
- **Model** — the effective `default_model` becomes the session's model when
  the agent is selected. `/model <name>` overrides it for the current session.
- **Thinking** — `default_thinking` (bool, default `false`) controls whether
  reasoning is requested by default for new sessions on this agent. `/think
  on|off` overrides per session.
- **`/system`** prints the resolved system prompt for the active session.

Pass an agent ID to `server.sh` to set the default, e.g.
`./server.sh user/r2`. Otherwise `default_agent` from `config.json`, then the
first agent alphabetically, is used.

## Configuration

`config.json` (json5 — trailing commas, comments, unquoted keys allowed). The
project-root `config.json` is the base layer for every agent, and is also
where server-level settings live:

```json
{
  "port": 8080,
  "mlx_url": "http://localhost:8081/v1/chat/completions",
  "default_agent": "user/r2",
  "default_model": "ollama:qwen2.5-coder:7b",
  "models": [
    "ollama:qwen2.5-coder:7b",
    "openai:gpt-4o-mini",
  ]
}
```

`port` and `mlx_url` are server-wide (read once at startup). `default_model`
and `models` cascade into agent resolution as described above.

`system.txt` at the project root is the base system prompt for every agent;
agent-specific `system.txt`, `agent.txt`, and `user.txt` files append to it.

## UI

- **Sessions** — left sidebar lists active sessions. `+ New yak` starts another,
  `×` closes one. Each session has its own history, model, stats, and thinking
  state.
- **Agent picker** — shown at the top of an empty session; hides on the first
  message. Switching agents updates the session's model to the agent's
  default. The active agent and model are shown as header badges.
- **Multi-line input** — Enter sends, Shift+Enter inserts a newline. The
  textarea grows up to 200px then scrolls.
- **Agent bubbles** — text between `###` markers becomes its own orange
  collapsible bubble with a 🤖 icon. The first whitespace-delimited token is
  used as the agent name in the header. On user messages the stripe sits on the
  right to match the right-aligned bubble. A user message that opens with `###`
  but isn't closed gets a newline + `###` appended automatically. Toggle
  visibility with `/agents [show|hide]`.
- **Thinking bubbles** — reasoning text (from a separate `thinking` field
  or inline `<think>...</think>` blocks) becomes its own gray italic
  collapsible bubble with a 💭 icon. Toggle visibility with `/think show|hide`.
  Leading/trailing whitespace is stripped from each bubble.

## Slash commands

| Command | Effect |
| --- | --- |
| `/new` | Start a new session, inheriting the active session's agent/model/think settings |
| `/close` | Close the current session |
| `/model` | Show the current model |
| `/model <name>` | Override the current session's model (agent unchanged) |
| `/models` | List the agent's available models, marking current and default |
| `/system` | Print the resolved system prompt for the current session |
| `/think` | Show thinking state and display mode |
| `/think on` / `/think off` | Enable/disable reasoning |
| `/think show` / `/think hide` | Show/hide reasoning output |
| `/agents` | Show agent-call bubble display mode |
| `/agents show` / `/agents hide` | Show/hide agent-call bubbles (default show) |
| `/time` | Last response duration, token count, tok/s |
| `/save <file>` | Write the current session to a file (must stay under cwd) |

Anything starting with `/` is intercepted; unknown commands print an error
instead of being sent to the model.

## API

- `GET /` — serves `www/index.html`. Any other GET path is served from `www/`;
  requests that resolve outside `www/` (path traversal, symlink escapes) return
  403.
- `GET /api/agents` — `{ agents: [{id, default_model, models}, ...], default_agent }`
- `GET /api/system?agent=<id>` — resolved system prompt for an agent
  (`{ agent, system_prompt }`). Empty `agent` returns the project-root prompt.
- `POST /api/chat` — `{ messages, agent, model, think }`. Streams `text/event-stream`
  with `{content}`, `{thinking}`, `{error}`, and a terminal
  `{done, eval_count, eval_duration}` event.
- `POST /api/save` — `{ file, content }`. Refuses paths outside the working
  directory.

## Files

- `server.sh` — launcher; cds to project root, sources `server.env`, runs
  `python3 -m src.server`
- `server.env` — API keys (`OLLAMA_API_KEY`, `OPENAI_API_KEY`, `MLX_API_KEY`).
  Treat as a secret — don't commit if it contains real keys.
- `src/server.py` — entry point: loads config, starts the HTTP server
- `src/handler.py` — HTTP routing, static serving, provider dispatch, SSE
  streaming, save endpoint
- `src/agents.py` — agent discovery and config/system-prompt resolution
- `src/config.py` — config loading, model-name parsing, reasoning-field
  extraction
- `www/index.html` — UI, session state, command handling, renderers
- `www/` — anything served to the browser must live under here
- `agents/` — agent definitions; each leaf directory is a selectable agent
- `config.json` — server-level config + base layer for every agent
- `system.txt` — optional base system prompt for every agent
