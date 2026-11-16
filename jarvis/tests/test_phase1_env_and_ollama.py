"""Phase 1 Tests: Environment detection, 6GB VRAM ResourceGuard & Ollama integration."""

from __future__ import annotations

import threading

import pytest

from jarvis.config.schema import OllamaConfig, ResourceConfig, STTConfig
from jarvis.models.model_manager import ModelManager
from jarvis.models.ollama_client import (
    OllamaClient,
    OllamaModelNotFoundError,
    OllamaUnavailableError,
)
from jarvis.security.emergency import CancellationToken, TaskCancelledError
from jarvis.system.env_probe import inspect_environment, sys_is_win11
from jarvis.system.monitor import SystemMonitor
from jarvis.system.resource_guard import ResourceGuard


def test_cloud_endpoint_is_strictly_rejected():
    """JARVIS must NEVER allow configuring a remote/cloud endpoint."""
    with pytest.raises(ValueError, match="local loopback"):
        OllamaConfig(base_url="https://api.openai.com/v1")
    with pytest.raises(ValueError, match="local loopback"):
        OllamaConfig(base_url="http://192.168.1.50:11434")

    # Loopback variants are accepted
    assert OllamaConfig(base_url="http://127.0.0.1:11434").base_url == "http://127.0.0.1:11434"
    assert OllamaConfig(base_url="http://localhost:11434/").base_url == "http://localhost:11434"


def test_model_discovery_embedding_filter_and_vram_scoring(mock_ollama):
    base_url, _state = mock_ollama
    mm = ModelManager(OllamaConfig(base_url=base_url), ResourceConfig(gpu_vram_total_mb=6144))

    ready, msg = mm.verify_ready()
    assert ready is True
    assert "qwen2.5:7b" in msg
    assert mm.active_model == "qwen2.5:7b"

    # Embedding model must be excluded from chat-compatible selector
    compat_names = [m.name for m in mm.compatible_models]
    assert "qwen2.5:7b" in compat_names
    assert "qwen2.5:3b" in compat_names
    assert "nomic-embed-text:latest" not in compat_names

    # Verify 6GB VRAM fit classifications
    by_name = {m.name: m for m in mm.compatible_models}
    assert by_name["qwen2.5:7b"].vram_fit == "optimal_6gb"
    assert by_name["qwen2.5:3b"].vram_fit == "coexist_gpu"


def test_model_switching_unloads_previous_vram(mock_ollama):
    base_url, state = mock_ollama
    mm = ModelManager(OllamaConfig(base_url=base_url))
    mm.refresh_models()
    assert mm.active_model == "qwen2.5:7b"

    switched = mm.select_model("qwen2.5:3b")
    assert switched.name == "qwen2.5:3b"
    assert mm.active_model == "qwen2.5:3b"
    assert "qwen2.5:7b" in state.unloaded_models

    # Selecting embedding or nonexistent model fails cleanly
    with pytest.raises(OllamaModelNotFoundError):
        mm.select_model("nomic-embed-text:latest")
    with pytest.raises(OllamaModelNotFoundError):
        mm.select_model("nonexistent:70b")


def test_streaming_chat_and_oom_context_recovery(mock_ollama):
    base_url, state = mock_ollama
    state.scripted_replies.append("Streaming works locally on RTX 3050!")
    state.fail_oom_if_ctx_above = 2048  # Force OOM on initial 4096 ctx

    client = OllamaClient(OllamaConfig(base_url=base_url, num_ctx=4096, low_vram_num_ctx=2048))
    streamed_tokens: list[str] = []
    res = client.chat(
        "qwen2.5:7b",
        [{"role": "user", "content": "test"}],
        on_token=streamed_tokens.append,
    )
    assert res.content == "Streaming works locally on RTX 3050!"
    assert "".join(streamed_tokens) == res.content
    assert res.used_num_ctx == 2048  # Automatically downgraded from 4096 on OOM!


def test_mid_stream_cancellation(mock_ollama):
    base_url, state = mock_ollama
    state.scripted_replies.append("A" * 200)
    state.stream_chunk_delay_sec = 0.08

    client = OllamaClient(OllamaConfig(base_url=base_url))
    token = CancellationToken()
    timer = threading.Timer(0.10, lambda: token.cancel("User stopped stream"))
    timer.start()
    try:
        with pytest.raises(TaskCancelledError):
            client.chat("qwen2.5:7b", [{"role": "user", "content": "long reply"}], token=token)
    finally:
        timer.cancel()


def test_offline_ollama_error_recovery():
    cfg = OllamaConfig(base_url="http://127.0.0.1:59999", connect_timeout_sec=1.0, max_retries=0)
    mm = ModelManager(cfg)
    ok, msg = mm.verify_ready()
    assert ok is False
    assert "not reachable" in msg.lower()
    with pytest.raises(OllamaUnavailableError):
        mm.chat([{"role": "user", "content": "hello"}])


def test_environment_inspector_and_resource_guard(sandbox_config):
    mon = SystemMonitor()
    rg = ResourceGuard(sandbox_config.resources, sandbox_config.ollama, sandbox_config.stt, mon)
    stt_dev, stt_comp = rg.resolve_stt_device()
    assert stt_dev == "cpu"
    assert stt_comp == "int8"

    report = inspect_environment(sandbox_config, monitor=mon)
    assert report["ollama"]["online"] is True
    assert report["hardware_and_os"]["ram_total_gb"] > 0
    assert report["packages"]["httpx"]["installed"] is True
    assert sys_is_win11("10.0.22631") is True
    assert sys_is_win11("10.0.19045") is False
