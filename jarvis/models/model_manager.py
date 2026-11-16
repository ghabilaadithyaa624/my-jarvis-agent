"""Stateful Ollama ModelManager with 6GB-VRAM scoring & safe model switching."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from jarvis.config.loader import load_models_catalog
from jarvis.config.schema import OllamaConfig, ResourceConfig
from jarvis.logs.logger import get_logger
from jarvis.models.ollama_client import (
    ChatResponse,
    LocalModelInfo,
    OllamaClient,
    OllamaModelNotFoundError,
    OllamaUnavailableError,
    RunningModelStatus,
)
from jarvis.security.emergency import CancellationToken

log = get_logger("models.manager")


class ModelManager:
    """Discovers, validates, scores, and switches local Ollama models safely."""

    def __init__(
        self,
        ollama_cfg: OllamaConfig,
        resource_cfg: Optional[ResourceConfig] = None,
        client: Optional[OllamaClient] = None,
    ) -> None:
        self.config = ollama_cfg
        self.resource_cfg = resource_cfg or ResourceConfig()
        self.client = client or OllamaClient(ollama_cfg)
        self._catalog = load_models_catalog()
        self._lock = threading.RLock()
        self._installed: List[LocalModelInfo] = []
        self._active_model: str = ""
        self._online: bool = False
        self._version: Optional[str] = None
        self._last_error: Optional[str] = None

    @property
    def active_model(self) -> str:
        with self._lock:
            return self._active_model

    @property
    def is_online(self) -> bool:
        with self._lock:
            return self._online

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def installed_models(self) -> List[LocalModelInfo]:
        with self._lock:
            return list(self._installed)

    @property
    def compatible_models(self) -> List[LocalModelInfo]:
        with self._lock:
            return [m for m in self._installed if m.is_chat_compatible]

    def _annotate_vram_fit(self, model: LocalModelInfo) -> None:
        """Score how well a local model fits the user's RTX 3050 6GB VRAM + 16GB RAM."""
        usable_mb = max(
            1024,
            self.resource_cfg.gpu_vram_total_mb - self.resource_cfg.gpu_vram_reserve_os_mb,
        )
        size_mb = int(model.size_bytes / (1024 * 1024)) if model.size_bytes else 0
        # Reserve ~450 MB for 4K KV cache
        est_vram_mb = size_mb + 450 if size_mb else 0

        profiles: Dict[str, Any] = self._catalog.get("recommended_profiles", {})
        matched_profile = profiles.get(model.name)
        if not matched_profile:
            # Also match base tag e.g. qwen2.5:7b-instruct-q4_K_M -> qwen2.5:7b
            for key, prof in profiles.items():
                if model.name.lower().startswith(key.lower()):
                    matched_profile = prof
                    break

        if model.is_embedding:
            model.vram_fit = "embedding_only"
            model.notes = "Embedding model (not selectable as conversational operator)."
            return

        if est_vram_mb and est_vram_mb <= 2800:
            model.vram_fit = "coexist_gpu"
            model.notes = f"~{est_vram_mb} MB VRAM — fast & safe alongside Blender/GPU apps."
        elif est_vram_mb and est_vram_mb <= usable_mb:
            model.vram_fit = "optimal_6gb"
            model.notes = f"~{est_vram_mb} MB VRAM — fits cleanly in RTX 3050 6GB."
        elif est_vram_mb and est_vram_mb <= usable_mb + 3500:
            model.vram_fit = "hybrid_offload"
            model.notes = f"~{est_vram_mb} MB — partial GPU + 16GB CPU RAM offload (moderate speed)."
        elif est_vram_mb > usable_mb + 3500:
            model.vram_fit = "heavy_offload"
            model.notes = f"~{est_vram_mb} MB — exceeds 6GB VRAM significantly; expect slow generation."
        else:
            model.vram_fit = "unknown"

        if matched_profile and matched_profile.get("notes"):
            model.notes = str(matched_profile["notes"])

    def refresh_models(self) -> List[LocalModelInfo]:
        """Probe Ollama, list all installed local models, and auto-pick if needed."""
        health = self.client.check_health()
        if not health["online"]:
            with self._lock:
                self._online = False
                self._version = None
                self._last_error = health["error"]
            log.warning("Local Ollama offline at %s: %s", self.client.base_url, health["error"])
            return []

        emb_patterns = self._catalog.get("embedding_only_patterns", [])
        try:
            models = self.client.list_models(embedding_patterns=emb_patterns)
        except OllamaUnavailableError as exc:
            with self._lock:
                self._online = False
                self._last_error = str(exc)
            return []

        for m in models:
            self._annotate_vram_fit(m)

        with self._lock:
            self._online = True
            self._version = health.get("version")
            self._last_error = None
            self._installed = models
            chat_names = {m.name for m in models if m.is_chat_compatible}
            if self._active_model not in chat_names:
                self._active_model = self._choose_best_model(models)

        log.info(
            "Detected %d Ollama model(s) (%d chat-compatible); active='%s'",
            len(models),
            len([m for m in models if m.is_chat_compatible]),
            self._active_model or "NONE",
        )
        return models

    def _choose_best_model(self, models: List[LocalModelInfo]) -> str:
        chat_models = [m for m in models if m.is_chat_compatible]
        if not chat_models:
            return ""
        by_name = {m.name.lower(): m.name for m in chat_models}

        # 1. Exact or prefix match for user's configured preferred_model
        pref = (self.config.preferred_model or "").strip().lower()
        if pref:
            if pref in by_name:
                return by_name[pref]
            for m in chat_models:
                if m.name.lower().startswith(pref):
                    return m.name

        # 2. Priority fallback list tuned for RTX 3050 6GB
        for fb in self.config.fallback_models:
            fbl = fb.lower()
            if fbl in by_name:
                return by_name[fbl]
            for m in chat_models:
                if m.name.lower().startswith(fbl):
                    return m.name

        # 3. Prefer optimal_6gb > coexist_gpu > hybrid_offload > heavy_offload
        rank = {
            "optimal_6gb": 0,
            "coexist_gpu": 1,
            "unknown": 2,
            "hybrid_offload": 3,
            "heavy_offload": 4,
        }
        chat_models.sort(key=lambda m: (rank.get(m.vram_fit, 5), m.size_bytes))
        return chat_models[0].name

    def verify_ready(self) -> Tuple[bool, str]:
        """Verify Ollama is reachable and the selected model is actually available locally."""
        models = self.refresh_models()
        if not self.is_online:
            return (
                False,
                f"Ollama is not reachable at {self.client.base_url}. "
                "Start Ollama locally (run 'ollama serve' or open the Ollama tray app).",
            )
        chat_models = [m for m in models if m.is_chat_compatible]
        if not chat_models:
            return (
                False,
                "Ollama is running, but no chat-capable local models are installed. "
                "Install one with: ollama pull qwen2.5:7b",
            )
        if not self.active_model:
            return False, "No active Ollama model selected."
        return True, f"Ollama v{self._version or '?'} ready with local model '{self.active_model}'."

    def select_model(self, model_name: str, unload_previous: Optional[bool] = None) -> LocalModelInfo:
        """Switch to another installed compatible local model and free prior VRAM."""
        target = (model_name or "").strip()
        if not target:
            raise OllamaModelNotFoundError("Model name cannot be empty.")

        models = self.refresh_models() if not self._installed else self.installed_models
        matched: Optional[LocalModelInfo] = None
        for m in models:
            if m.name == target or m.name.lower() == target.lower():
                matched = m
                break
        if matched is None:
            # Re-probe in case user just pulled a model
            models = self.refresh_models()
            for m in models:
                if m.name == target or m.name.lower() == target.lower():
                    matched = m
                    break
        if matched is None:
            raise OllamaModelNotFoundError(
                f"Model '{target}' is not installed in local Ollama. "
                f"Available: {', '.join(m.name for m in self.compatible_models) or 'none'}."
            )
        if not matched.is_chat_compatible:
            raise OllamaModelNotFoundError(
                f"Model '{matched.name}' is an embedding-only model and cannot be used for chat."
            )

        with self._lock:
            previous = self._active_model

        should_unload = (
            self.config.unload_previous_on_switch
            if unload_previous is None
            else unload_previous
        )
        if should_unload and previous and previous != matched.name:
            log.info("Unloading previous model '%s' to free RTX 3050 VRAM...", previous)
            self.client.unload_model(previous)

        with self._lock:
            self._active_model = matched.name
            self.config.preferred_model = matched.name

        log.info("Switched active Ollama model to '%s' (%s)", matched.name, matched.vram_fit)
        return matched

    def unload_active_model(self) -> bool:
        """Manually release GPU VRAM (useful right before launching heavy Blender renders)."""
        current = self.active_model
        return self.client.unload_model(current) if current else False

    def get_running_models(self) -> List[RunningModelStatus]:
        return self.client.list_running()

    def get_status_snapshot(self) -> Dict[str, Any]:
        active = self.active_model
        active_info = next((m.to_dict() for m in self.installed_models if m.name == active), None)
        running = self.get_running_models() if self.is_online else []
        return {
            "online": self.is_online,
            "base_url": self.client.base_url,
            "version": self._version,
            "active_model": active,
            "active_model_info": active_info,
            "compatible_models": [m.to_dict() for m in self.compatible_models],
            "running_vram_mb": sum(r.vram_mb for r in running),
            "error": self.last_error,
        }

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        num_ctx: Optional[int] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        token: Optional[CancellationToken] = None,
    ) -> ChatResponse:
        model = self.active_model
        if not model:
            ok, msg = self.verify_ready()
            if not ok:
                raise OllamaUnavailableError(msg)
            model = self.active_model
        return self.client.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            num_ctx=num_ctx,
            max_tokens=max_tokens,
            json_mode=json_mode,
            on_token=on_token,
            token=token,
        )
