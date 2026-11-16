"""System telemetry, resource budgeting, environment inspection & Windows controls."""

from jarvis.system.controls import (
    PROTECTED_PROCESS_NAMES,
    WINDOWS_SETTINGS_URIS,
    SystemController,
)
from jarvis.system.env_probe import inspect_environment
from jarvis.system.monitor import GPUStatus, SystemMonitor, SystemSnapshot
from jarvis.system.resource_guard import ResourceGuard

__all__ = [
    "GPUStatus",
    "PROTECTED_PROCESS_NAMES",
    "ResourceGuard",
    "SystemController",
    "SystemMonitor",
    "SystemSnapshot",
    "WINDOWS_SETTINGS_URIS",
    "inspect_environment",
]
