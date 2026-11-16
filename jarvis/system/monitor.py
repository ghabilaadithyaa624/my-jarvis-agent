"""Hardware specification reader, live CPU/RAM/GPU/Disk telemetry & diagnostics."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import psutil


@dataclass
class GPUStatus:
    available: bool = False
    name: str = "Not detected"
    vram_total_mb: int = 0
    vram_used_mb: int = 0
    vram_free_mb: int = 0
    utilization_percent: float = 0.0
    temperature_c: Optional[float] = None
    driver_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "name": self.name,
            "vram_total_mb": self.vram_total_mb,
            "vram_used_mb": self.vram_used_mb,
            "vram_free_mb": self.vram_free_mb,
            "vram_percent": round((self.vram_used_mb / self.vram_total_mb) * 100.0, 1)
            if self.vram_total_mb
            else 0.0,
            "utilization_percent": self.utilization_percent,
            "temperature_c": self.temperature_c,
            "driver_version": self.driver_version,
        }


@dataclass
class SystemSnapshot:
    timestamp: float
    cpu_percent: float
    cpu_freq_mhz: Optional[float]
    ram_total_mb: int
    ram_used_mb: int
    ram_available_mb: int
    ram_percent: float
    swap_percent: float
    gpu: GPUStatus
    battery_percent: Optional[float] = None
    battery_plugged: Optional[bool] = None
    disks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cpu_percent": self.cpu_percent,
            "cpu_freq_mhz": self.cpu_freq_mhz,
            "ram_total_mb": self.ram_total_mb,
            "ram_used_mb": self.ram_used_mb,
            "ram_available_mb": self.ram_available_mb,
            "ram_percent": self.ram_percent,
            "swap_percent": self.swap_percent,
            "gpu": self.gpu.to_dict(),
            "battery_percent": self.battery_percent,
            "battery_plugged": self.battery_plugged,
            "disks": self.disks,
        }


class SystemMonitor:
    """Queries CPU, RAM, NVIDIA RTX GPU (via nvidia-smi), storage, battery & processes."""

    def __init__(self) -> None:
        self._nvidia_smi = self._find_nvidia_smi()
        # Prime psutil cpu_percent so later non-blocking reads are accurate
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

    @staticmethod
    def _find_nvidia_smi() -> Optional[str]:
        found = shutil.which("nvidia-smi")
        if found:
            return found
        if os.name == "nt":
            win_candidates = [
                r"C:\Windows\System32\nvidia-smi.exe",
                r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
            ]
            for c in win_candidates:
                if os.path.isfile(c):
                    return c
        return None

    def query_gpu(self) -> GPUStatus:
        """Read NVIDIA GPU utilization, VRAM usage, and temperature without holding CUDA context!

        Important optimization: using nvidia-smi instead of torch.cuda leaves 0 MB
        CUDA context overhead on the RTX 3050 6GB GPU.
        """
        if not self._nvidia_smi:
            return GPUStatus(available=False)

        cmd = [
            self._nvidia_smi,
            "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,driver_version",
            "--format=csv,noheader,nounits",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3.0,
                creationflags=creationflags,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return GPUStatus(available=False)
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) < 7:
                return GPUStatus(available=False)

            def _int(val: str) -> int:
                try:
                    return int(float(val))
                except ValueError:
                    return 0

            def _float(val: str) -> Optional[float]:
                try:
                    return round(float(val), 1)
                except ValueError:
                    return None

            return GPUStatus(
                available=True,
                name=parts[0],
                vram_total_mb=_int(parts[1]),
                vram_used_mb=_int(parts[2]),
                vram_free_mb=_int(parts[3]),
                utilization_percent=_float(parts[4]) or 0.0,
                temperature_c=_float(parts[5]),
                driver_version=parts[6],
            )
        except Exception:
            return GPUStatus(available=False)

    def get_cpu_name(self) -> str:
        if os.name == "nt":
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                ) as key:
                    val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                    if val:
                        return str(val).strip()
            except Exception:
                pass
        elif os.path.isfile("/proc/cpuinfo"):
            try:
                with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if line.lower().startswith("model name"):
                            return line.split(":", 1)[1].strip()
            except Exception:
                pass
        return platform.processor() or platform.machine() or "Unknown CPU"

    def get_system_specs(self) -> Dict[str, Any]:
        """Return detailed hardware and OS specifications."""
        vm = psutil.virtual_memory()
        freq = None
        try:
            f = psutil.cpu_freq()
            if f:
                freq = {"current_mhz": round(f.current, 0), "max_mhz": round(f.max, 0)}
        except Exception:
            freq = None

        gpu = self.query_gpu()
        boot_ts = psutil.boot_time()
        uptime_hours = round((time.time() - boot_ts) / 3600.0, 2)

        return {
            "os": f"{platform.system()} {platform.release()}",
            "os_version": platform.version(),
            "hostname": platform.node(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "cpu_model": self.get_cpu_name(),
            "cpu_physical_cores": psutil.cpu_count(logical=False) or 0,
            "cpu_logical_threads": psutil.cpu_count(logical=True) or 0,
            "cpu_frequency": freq,
            "ram_total_gb": round(vm.total / (1024 ** 3), 2),
            "gpu": gpu.to_dict(),
            "disks": self.get_disk_space(),
            "uptime_hours": uptime_hours,
        }

    def get_disk_space(self) -> List[Dict[str, Any]]:
        """Read storage capacity and free space across accessible partitions."""
        disks: List[Dict[str, Any]] = []
        seen_mounts: set[str] = set()
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint in seen_mounts:
                continue
            if os.name == "nt" and ("cdrom" in part.opts.lower() or not part.fstype):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            seen_mounts.add(part.mountpoint)
            disks.append(
                {
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 2),
                    "used_gb": round(usage.used / (1024 ** 3), 2),
                    "free_gb": round(usage.free / (1024 ** 3), 2),
                    "percent_used": round(usage.percent, 1),
                }
            )
        return disks

    def snapshot(self, sample_interval: float = 0.0, include_disks: bool = False) -> SystemSnapshot:
        """Capture live CPU, RAM, GPU, battery, and optional disk metrics."""
        cpu_pct = psutil.cpu_percent(interval=sample_interval)
        freq_mhz: Optional[float] = None
        try:
            f = psutil.cpu_freq()
            if f and f.current:
                freq_mhz = round(f.current, 0)
        except Exception:
            pass

        vm = psutil.virtual_memory()
        try:
            sw = psutil.swap_memory()
            swap_pct = round(sw.percent, 1)
        except Exception:
            swap_pct = 0.0

        bat_pct: Optional[float] = None
        bat_plugged: Optional[bool] = None
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                bat_pct = round(bat.percent, 1)
                bat_plugged = bool(bat.power_plugged)
        except Exception:
            pass

        gpu = self.query_gpu()
        jarvis_proc = psutil.Process(os.getpid())
        _ = jarvis_proc  # available for self-telemetry in diagnostics

        return SystemSnapshot(
            timestamp=time.time(),
            cpu_percent=round(cpu_pct, 1),
            cpu_freq_mhz=freq_mhz,
            ram_total_mb=int(vm.total / (1024 * 1024)),
            ram_used_mb=int(vm.used / (1024 * 1024)),
            ram_available_mb=int(vm.available / (1024 * 1024)),
            ram_percent=round(vm.percent, 1),
            swap_percent=swap_pct,
            gpu=gpu,
            battery_percent=bat_pct,
            battery_plugged=bat_plugged,
            disks=self.get_disk_space() if include_disks else [],
        )

    def get_top_processes(self, limit: int = 15, sort_by: str = "memory") -> List[Dict[str, Any]]:
        """Return currently running processes ranked by RAM or CPU consumption."""
        procs: List[Dict[str, Any]] = []
        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "status"]):
            try:
                info = p.info
                mem_info = info.get("memory_info")
                rss_mb = round((mem_info.rss / (1024 * 1024)), 1) if mem_info else 0.0
                procs.append(
                    {
                        "pid": info["pid"],
                        "name": info.get("name") or "unknown",
                        "username": info.get("username") or "",
                        "cpu_percent": round(float(info.get("cpu_percent") or 0.0), 1),
                        "memory_mb": rss_mb,
                        "status": info.get("status") or "",
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if sort_by == "cpu":
            procs.sort(key=lambda x: (x["cpu_percent"], x["memory_mb"]), reverse=True)
        else:
            procs.sort(key=lambda x: (x["memory_mb"], x["cpu_percent"]), reverse=True)
        return procs[: max(1, min(limit, 100))]

    def get_jarvis_self_usage(self) -> Dict[str, Any]:
        """Monitor Jarvis's own RAM and CPU consumption."""
        try:
            proc = psutil.Process(os.getpid())
            rss_mb = round(proc.memory_info().rss / (1024 * 1024), 1)
            cpu = round(proc.cpu_percent(interval=None), 1)
            threads = proc.num_threads()
            return {"pid": proc.pid, "rss_mb": rss_mb, "cpu_percent": cpu, "threads": threads}
        except Exception:
            return {"pid": os.getpid(), "rss_mb": 0.0, "cpu_percent": 0.0, "threads": 1}

    def run_diagnostics(self) -> Dict[str, Any]:
        """Generate a comprehensive laptop health & bottleneck analysis."""
        snap = self.snapshot(sample_interval=0.15, include_disks=True)
        top_mem = self.get_top_processes(limit=8, sort_by="memory")
        warnings: List[str] = []
        recommendations: List[str] = []

        if snap.cpu_percent >= 85.0:
            warnings.append(f"High CPU load ({snap.cpu_percent}%).")
            recommendations.append("Check top CPU consumers or close heavy background builds/tabs.")
        if snap.ram_percent >= 85.0:
            warnings.append(
                f"High RAM pressure ({snap.ram_percent}% — only {snap.ram_available_mb} MB available)."
            )
            recommendations.append(
                "Close idle browser tabs or switch Ollama to a compact 3B model (e.g. qwen2.5:3b)."
            )
        if snap.gpu.available:
            if snap.gpu.vram_free_mb < 800:
                warnings.append(
                    f"Low GPU VRAM ({snap.gpu.vram_free_mb} MB free of {snap.gpu.vram_total_mb} MB on {snap.gpu.name})."
                )
                recommendations.append(
                    "Reduced context window (2048) or a 3B model is recommended while GPU-heavy apps like Blender run."
                )
            if snap.gpu.temperature_c and snap.gpu.temperature_c >= 83.0:
                warnings.append(f"Elevated GPU temperature ({snap.gpu.temperature_c}°C).")
                recommendations.append("Ensure laptop vents are unobstructed and power profile allows full fan curve.")
        for disk in snap.disks:
            if disk["percent_used"] >= 90.0 or disk["free_gb"] < 15.0:
                warnings.append(
                    f"Low disk space on {disk['mountpoint']} ({disk['free_gb']} GB free, {disk['percent_used']}% used)."
                )
                recommendations.append(
                    f"Run find_duplicates or clean temporary/old downloads on {disk['mountpoint']}."
                )
        if snap.battery_percent is not None and not snap.battery_plugged and snap.battery_percent < 25.0:
            warnings.append(f"Low battery ({snap.battery_percent}%, unplugged).")
            recommendations.append("Connect charger for sustained local LLM performance on RTX GPU.")

        health = "HEALTHY" if not warnings else ("ATTENTION" if len(warnings) <= 2 else "DEGRADED")
        return {
            "overall_health": health,
            "snapshot": snap.to_dict(),
            "jarvis_footprint": self.get_jarvis_self_usage(),
            "top_memory_processes": top_mem,
            "warnings": warnings,
            "recommendations": recommendations,
        }
