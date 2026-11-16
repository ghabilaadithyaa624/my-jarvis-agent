"""VRAM and RAM resource budget optimizer for RTX 3050 6GB + 16GB RAM."""

from __future__ import annotations

from typing import Dict, Tuple

from jarvis.config.schema import OllamaConfig, ResourceConfig, STTConfig
from jarvis.system.monitor import SystemMonitor


class ResourceGuard:
    """Dynamically prevents OOM and concurrent GPU contention on 6GB VRAM hardware."""

    def __init__(
        self,
        resource_cfg: ResourceConfig,
        ollama_cfg: OllamaConfig,
        stt_cfg: STTConfig,
        monitor: SystemMonitor,
    ) -> None:
        self.resource_cfg = resource_cfg
        self.ollama_cfg = ollama_cfg
        self.stt_cfg = stt_cfg
        self.monitor = monitor

    def recommended_num_ctx(self) -> int:
        """Lower Ollama context size automatically when VRAM or RAM is constrained."""
        snap = self.monitor.snapshot(sample_interval=0.0)
        if snap.ram_percent >= 88.0:
            return self.ollama_cfg.low_vram_num_ctx
        if snap.gpu.available and snap.gpu.vram_free_mb < 1100:
            return self.ollama_cfg.low_vram_num_ctx
        return self.ollama_cfg.num_ctx

    def resolve_stt_device(self) -> Tuple[str, str]:
        """Choose (device, compute_type) for faster-whisper with automatic CPU fallback.

        Defaults to ('cpu', 'int8') on the i5-13420H so 100% of the RTX 3050 6GB
        VRAM remains dedicated to Ollama. Only permits CUDA if explicitly enabled
        AND free VRAM exceeds the configured safety reserve.
        """
        if self.stt_cfg.device == "cpu":
            return "cpu", "int8"

        gpu = self.monitor.query_gpu()
        if not gpu.available:
            return "cpu", "int8"

        if gpu.vram_free_mb >= self.resource_cfg.gpu_vram_min_free_for_cuda_stt_mb:
            try:
                import ctranslate2

                if ctranslate2.get_cuda_device_count() > 0:
                    return "cuda", "int8_float16"
            except Exception:
                pass
        return "cpu", "int8"

    def evaluate_budget(self) -> Dict[str, object]:
        """Return a transparent hardware allocation report."""
        snap = self.monitor.snapshot(sample_interval=0.0)
        stt_device, stt_compute = self.resolve_stt_device()
        ctx = self.recommended_num_ctx()
        alerts = []
        if snap.ram_percent >= self.resource_cfg.ram_warn_percent:
            alerts.append(f"RAM usage is {snap.ram_percent}% (threshold {self.resource_cfg.ram_warn_percent}%).")
        if snap.cpu_percent >= self.resource_cfg.cpu_warn_percent:
            alerts.append(f"CPU usage is {snap.cpu_percent}% (threshold {self.resource_cfg.cpu_warn_percent}%).")
        if snap.gpu.available and snap.gpu.vram_free_mb < 800:
            alerts.append(f"GPU free VRAM is low ({snap.gpu.vram_free_mb} MB).")

        return {
            "recommended_ollama_num_ctx": ctx,
            "stt_device": stt_device,
            "stt_compute_type": stt_compute,
            "tts_device": "cpu",
            "ram_percent": snap.ram_percent,
            "ram_available_mb": snap.ram_available_mb,
            "gpu_available": snap.gpu.available,
            "gpu_vram_free_mb": snap.gpu.vram_free_mb,
            "alerts": alerts,
        }
