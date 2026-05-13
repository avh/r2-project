#!/usr/bin/env python3
"""Entry point: load config, start the HTTP server."""

import sys
import warnings

warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL")

from pathlib import Path

from .agents import list_agents
from .config import load_config
from .handler import ChatServer, Handler


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WWW_ROOT = (PROJECT_ROOT / "www").resolve()
AGENTS_ROOT = (PROJECT_ROOT / "agents").resolve()
CONFIG_PATH = PROJECT_ROOT / "config.json"


def main():
    server_config = load_config(CONFIG_PATH)
    port = server_config["port"]
    mlx_url = server_config["mlx_url"]

    agents = list_agents(AGENTS_ROOT)
    default_agent = sys.argv[1] if len(sys.argv) > 1 else server_config.get("default_agent", "")
    if default_agent and default_agent not in agents:
        sys.stderr.write(f"[warn] unknown default agent '{default_agent}'; available: {agents}\n")
        default_agent = ""
    if not default_agent and agents:
        default_agent = agents[0]

    server = ChatServer(
        ("localhost", port),
        Handler,
        mlx_url=mlx_url,
        www_root=WWW_ROOT,
        save_root=Path.cwd().resolve(),
        project_root=PROJECT_ROOT,
        agents_root=AGENTS_ROOT,
        default_agent=default_agent,
    )

    print(f"Chat bot running at http://localhost:{port}")
    if agents:
        print(f"  agents: {', '.join(agents)}")
        if default_agent:
            print(f"  default: {default_agent}")
    else:
        print(f"  no agents found under {AGENTS_ROOT.relative_to(PROJECT_ROOT)}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
