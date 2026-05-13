"""HTTP request handler, chat server, and provider streaming."""

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import requests

from .agents import agent_info, list_agents, resolve_agent
from .config import OMLX_ORG, extract_thinking, parse_model


LOCAL_URL = "http://localhost:11434/api/chat"
OLLAMA_CLOUD_URL = "https://ollama.com/api/chat"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class ChatServer(HTTPServer):
    def __init__(self, addr, handler_cls, *, mlx_url, www_root, save_root,
                 project_root, agents_root, default_agent):
        super().__init__(addr, handler_cls)
        self.mlx_url = mlx_url
        self.www_root = www_root
        self.save_root = save_root
        self.project_root = project_root
        self.agents_root = agents_root
        self.default_agent = default_agent


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress request logging

    def do_GET(self):
        split = urlsplit(self.path)
        path = split.path
        if path == "/api/agents":
            self._handle_agents()
            return
        if path == "/api/system":
            self._handle_system(parse_qs(split.query))
            return
        self._serve_static(path)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _handle_agents(self):
        project_root = self.server.project_root
        agents_root = self.server.agents_root
        ids = list_agents(agents_root)
        agents = [agent_info(project_root, agents_root, a) for a in ids]
        default_agent = self.server.default_agent
        if default_agent not in ids:
            default_agent = ids[0] if ids else ""
        self._send_json({"agents": agents, "default_agent": default_agent})

    def _handle_system(self, query):
        agent_id = (query.get("agent", [""])[0] or "").strip()
        agents_root = self.server.agents_root
        if agent_id and agent_id not in list_agents(agents_root):
            self.send_error(404, "Unknown agent")
            return
        resolved = resolve_agent(self.server.project_root, agents_root, agent_id)
        self._send_json({
            "agent": agent_id,
            "system_prompt": resolved["system_prompt"],
        })

    def _serve_static(self, request_path):
        rel = unquote(request_path).lstrip("/")
        if not rel:
            rel = "index.html"
        www_root = self.server.www_root
        # Resolve symlinks and `..` segments, then confirm the result still
        # lives under www_root. This blocks path traversal and symlink escapes.
        try:
            target = (www_root / rel).resolve()
            target.relative_to(www_root)
        except (ValueError, OSError):
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(str(target))
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if self.path == "/api/save":
            self._save_session()
            return
        if self.path != "/api/chat":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        messages = body["messages"]
        agent_id = (body.get("agent") or "").strip()
        agents_root = self.server.agents_root
        if agent_id and agent_id not in list_agents(agents_root):
            self._send_sse_headers()
            self._send_event({"error": f"Unknown agent: {agent_id}"})
            self._send_event({"done": True, "eval_count": 0, "eval_duration": 0})
            return

        resolved = resolve_agent(self.server.project_root, agents_root, agent_id)
        system_prompt = resolved["system_prompt"]
        default_model = resolved["config"].get("default_model") or ""

        model = (body.get("model") or "").strip() or default_model
        if not model:
            self._send_sse_headers()
            self._send_event({"error": "No model specified for this agent."})
            self._send_event({"done": True, "eval_count": 0, "eval_duration": 0})
            return
        think = bool(body.get("think"))
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        provider, api_model = parse_model(model)
        if provider == "openai":
            self._stream_openai(messages, api_model)
        elif provider == "omlx":
            self._stream_mlx(messages, api_model)
        elif provider == "ollama-cloud":
            self._stream_ollama(messages, api_model, OLLAMA_CLOUD_URL, cloud=True, think=think)
        else:
            self._stream_ollama(messages, api_model, LOCAL_URL, cloud=False, think=think)

    def _save_session(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            filename = (body.get("file") or "").strip()
            content = body.get("content") or ""
        except (ValueError, AttributeError):
            self.send_error(400, "Invalid JSON body")
            return
        if not filename:
            self.send_error(400, "Missing 'file'")
            return

        save_root = self.server.save_root
        target = (save_root / filename).resolve()
        try:
            target.relative_to(save_root)
        except ValueError:
            self.send_error(400, "File path must stay inside the working directory")
            return

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            self.send_error(500, f"Could not write file: {e}")
            return

        rel = str(target.relative_to(save_root))
        sys.stderr.write(f"[save] wrote {len(content.encode('utf-8'))} bytes to {rel}\n")
        sys.stderr.flush()
        resp = json.dumps({"path": rel, "bytes": len(content.encode("utf-8"))}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def _send_sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _send_event(self, payload):
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    def _send_error(self, message):
        sys.stderr.write(f"[error] {message}\n")
        sys.stderr.flush()
        self._send_sse_headers()
        self._send_event({"error": message})
        self._send_event({"done": True, "eval_count": 0, "eval_duration": 0})

    def _stream_ollama(self, messages, model, url, cloud=False, think=False):
        headers = {}
        if cloud:
            api_key = os.environ.get("OLLAMA_API_KEY")
            if not api_key:
                self._send_error("OLLAMA_API_KEY environment variable not set")
                return
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {"model": model, "messages": messages, "stream": True}
        if think:
            payload["think"] = True

        label = "Ollama Cloud" if cloud else "Ollama"
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=60,
            )
        except requests.exceptions.ConnectionError:
            hint = "" if cloud else " Is it running?"
            self._send_error(f"Cannot connect to {label}.{hint}")
            return

        if resp.status_code != 200:
            self._send_error(f"{label} {resp.status_code}: {resp.text[:1000]} (model={model})")
            return

        self._send_sse_headers()

        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            msg = chunk.get("message", {})
            thinking = msg.get("thinking", "")
            content = msg.get("content", "")
            if thinking:
                self._send_event({"thinking": thinking})
            if content:
                self._send_event({"content": content})
            if chunk.get("done"):
                self._send_event({
                    "done": True,
                    "eval_count": chunk.get("eval_count", 0),
                    "eval_duration": chunk.get("eval_duration", 0),
                })
                break

    def _stream_openai(self, messages, model):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self._send_error("OPENAI_API_KEY environment variable not set")
            return
        self._stream_openai_compatible(
            messages, model, OPENAI_URL, "OpenAI",
            extra_headers={"Authorization": f"Bearer {api_key}"},
        )

    def _stream_mlx(self, messages, model):
        extra = {}
        api_key = os.environ.get("MLX_API_KEY")
        if api_key:
            extra["Authorization"] = f"Bearer {api_key}"
        full_model = model if "/" in model else OMLX_ORG + model
        self._stream_openai_compatible(
            messages, full_model, self.server.mlx_url, "MLX", extra_headers=extra,
        )

    def _stream_openai_compatible(self, messages, model, url, label, extra_headers=None):
        if not model:
            self._send_error("Empty model name.")
            return

        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        try:
            resp = requests.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
                stream=True,
                timeout=60,
            )
        except requests.exceptions.RequestException as e:
            self._send_error(f"Cannot reach {label}: {e}")
            return

        if resp.status_code != 200:
            try:
                err = resp.json().get("error") or {}
                msg = err.get("message") or resp.text
                code = err.get("code") or err.get("type") or ""
            except ValueError:
                msg = resp.text
                code = ""
            tag = f"{label} {resp.status_code}"
            if code:
                tag += f" [{code}]"
            self._send_error(f"{tag}: {msg.strip()[:1000]} (model={model})")
            return

        self._send_sse_headers()

        eval_count = 0
        for line in resp.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            payload = line[6:].decode()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            usage = chunk.get("usage")
            if usage:
                eval_count = usage.get("completion_tokens", eval_count)
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                thinking = extract_thinking(delta)
                if thinking:
                    self._send_event({"thinking": thinking})
                content = delta.get("content")
                if content:
                    self._send_event({"content": content})

        self._send_event({"done": True, "eval_count": eval_count, "eval_duration": 0})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
