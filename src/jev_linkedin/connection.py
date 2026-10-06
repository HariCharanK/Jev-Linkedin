"""Select the user's Brave debugger explicitly; never silently attach another browser."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def configure_connection():
    os.environ.setdefault("BU_NAME", "jev-linkedin")
    endpoint = os.environ.get("BU_CDP_WS")
    if endpoint:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("BU_CDP_WS must be a browser debugger WebSocket URL")
        return "explicit"
    active_port = Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser/DevToolsActivePort"
    try:
        lines = active_port.read_text().splitlines()
        port, path = int(lines[0]), lines[1].strip()
        if not 1 <= port <= 65535 or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", path):
            raise ValueError()
    except (OSError, ValueError, IndexError):
        raise RuntimeError(
            "Brave remote debugging is not configured. Enable it in Brave at "
            "brave://inspect/#remote-debugging, or supply BU_CDP_WS."
        ) from None
    os.environ["BU_CDP_WS"] = f"ws://127.0.0.1:{port}{path}"
    return "brave"
