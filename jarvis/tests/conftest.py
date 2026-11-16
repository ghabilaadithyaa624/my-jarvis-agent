"""Shared pytest fixtures including a real local loopback Mock Ollama HTTP Server."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

import pytest

from jarvis.config.loader import load_config
from jarvis.config.schema import JarvisConfig


class MockOllamaState:
    def __init__(self) -> None:
        self.version = "0.6.2"
        self.models: List[Dict[str, Any]] = [
            {
                "name": "qwen2.5:7b",
                "size": int(4.4 * (1024 ** 3)),
                "modified_at": "2026-11-15T10:00:00Z",
                "digest": "sha256:abc123",
                "details": {
                    "family": "qwen2",
                    "families": ["qwen2"],
                    "parameter_size": "7.6B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {
                "name": "qwen2.5:3b",
                "size": int(1.9 * (1024 ** 3)),
                "modified_at": "2026-11-15T09:00:00Z",
                "digest": "sha256:def456",
                "details": {
                    "family": "qwen2",
                    "families": ["qwen2"],
                    "parameter_size": "3.1B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {
                "name": "nomic-embed-text:latest",
                "size": int(0.27 * (1024 ** 3)),
                "modified_at": "2026-11-14T08:00:00Z",
                "digest": "sha256:emb789",
                "details": {
                    "family": "nomic-bert",
                    "families": ["nomic-bert"],
                    "parameter_size": "137M",
                    "quantization_level": "F16",
                },
            },
        ]
        self.scripted_replies: List[str] = []
        self.unloaded_models: List[str] = []
        self.chat_requests: List[Dict[str, Any]] = []
        self.fail_oom_if_ctx_above: int = 0
        self.stream_chunk_delay_sec: float = 0.0


def _make_handler(state: MockOllamaState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # Keep test output clean

        def _send_json(self, code: int, obj: Dict[str, Any]) -> None:
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/version":
                self._send_json(200, {"version": state.version})
            elif self.path == "/api/tags":
                self._send_json(200, {"models": state.models})
            elif self.path == "/api/ps":
                self._send_json(
                    200,
                    {
                        "models": [
                            {
                                "name": "qwen2.5:7b",
                                "size": int(4.6 * (1024 ** 3)),
                                "size_vram": int(4.6 * (1024 ** 3)),
                                "expires_at": "2026-11-16T12:00:00Z",
                            }
                        ]
                    },
                )
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")

            if self.path == "/api/show":
                target = body.get("name", "")
                if any(m["name"] == target for m in state.models):
                    self._send_json(200, {"modelfile": "", "details": {"parameter_size": "7B"}})
                else:
                    self._send_json(404, {"error": "model not found"})
                return

            if self.path == "/api/chat":
                if body.get("keep_alive") == 0 and not body.get("messages"):
                    state.unloaded_models.append(str(body.get("model", "")))
                    self._send_json(200, {"model": body.get("model"), "done": True})
                    return

                state.chat_requests.append(body)
                model = body.get("model", "")
                if not any(m["name"] == model for m in state.models):
                    self._send_json(404, {"error": f"model '{model}' not found"})
                    return

                ctx = int((body.get("options") or {}).get("num_ctx") or 4096)
                if state.fail_oom_if_ctx_above and ctx > state.fail_oom_if_ctx_above:
                    self._send_json(500, {"error": "CUDA error: out of memory allocating KV cache"})
                    return

                reply = (
                    state.scripted_replies.pop(0)
                    if state.scripted_replies
                    else "Hello! I am JARVIS running locally on Ollama."
                )

                # Stream NDJSON tokens just like real Ollama
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                step = max(8, len(reply) // 4)
                chunks = [reply[i : i + step] for i in range(0, len(reply), step)] or [""]
                try:
                    for ch in chunks:
                        if state.stream_chunk_delay_sec > 0:
                            time.sleep(state.stream_chunk_delay_sec)
                        line = json.dumps(
                            {"model": model, "message": {"role": "assistant", "content": ch}, "done": False}
                        ) + "\n"
                        self.wfile.write(line.encode("utf-8"))
                        self.wfile.flush()
                    done_line = json.dumps(
                        {
                            "model": model,
                            "message": {"role": "assistant", "content": ""},
                            "done": True,
                            "prompt_eval_count": 42,
                            "eval_count": len(chunks) * 3,
                        }
                    ) + "\n"
                    self.wfile.write(done_line.encode("utf-8"))
                    self.wfile.flush()
                except BrokenPipeError:
                    pass
                return

            self._send_json(404, {"error": "unknown route"})

    return Handler


@pytest.fixture
def mock_ollama():
    state = MockOllamaState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def sandbox_config(tmp_path: Path, mock_ollama) -> JarvisConfig:
    base_url, _state = mock_ollama
    data_dir = tmp_path / "jarvis_data"
    user_home = tmp_path / "user_files"
    for sub in ("Desktop", "Documents", "Downloads", "Projects"):
        (user_home / sub).mkdir(parents=True, exist_ok=True)

    cfg = load_config(data_dir_override=data_dir)
    cfg.ollama.base_url = base_url
    cfg.tts.muted = True
    cfg.security.permitted_directories = [
        str((user_home / "Desktop").resolve()),
        str((user_home / "Documents").resolve()),
        str((user_home / "Downloads").resolve()),
        str((user_home / "Projects").resolve()),
        str(data_dir.resolve()),
        str((data_dir / "workspace").resolve()),
        str((data_dir / "screenshots").resolve()),
    ]
    return cfg


@pytest.fixture
def security_gate(sandbox_config, tmp_path: Path):
    """A bare SecurityGate wired to a real hash-chained audit log (no tools)."""
    from jarvis.logs.logger import AuditLogger
    from jarvis.security.emergency import EmergencyStopController
    from jarvis.security.gate import SecurityGate

    workspace = sandbox_config.resolved_data_dir() / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    audit = AuditLogger(sandbox_config.resolved_data_dir() / "logs" / "audit.jsonl")
    gate = SecurityGate(
        security_cfg=sandbox_config.security,
        audit_logger=audit,
        emergency=EmergencyStopController(),
        confirmation_handler=None,
        workspace_dir=workspace,
    )
    gate.audit_log = audit  # convenience handle for assertions
    return gate


@pytest.fixture
def controller(sandbox_config):
    """A fully wired JarvisController with no background services and no confirmation handler."""
    from jarvis.app.controller import JarvisController

    ctrl = JarvisController(sandbox_config, start_background_services=False)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
