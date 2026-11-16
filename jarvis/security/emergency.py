"""Emergency Stop kill-switch and per-task CancellationToken.

Allows instant interruption of streaming LLM requests, tool execution, spawned
child processes, voice synthesis, and active automation loops.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable, List, Optional, Set


class TaskCancelledError(RuntimeError):
    """Raised when a running task is cancelled by the user or Emergency Stop."""


class EmergencyStopError(TaskCancelledError):
    """Raised when the global Emergency Stop kill-switch is engaged."""


class CancellationToken:
    """Thread-safe cooperative cancellation token for a single task."""

    def __init__(self, reason: str = "") -> None:
        self._event = threading.Event()
        self._reason = reason
        self._callbacks: List[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason or "Cancelled by user."

    def cancel(self, reason: str = "Cancelled by user.") -> None:
        callbacks: List[Callable[[], None]] = []
        with self._lock:
            if not self._event.is_set():
                self._reason = reason
                self._event.set()
                callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

    def on_cancel(self, callback: Callable[[], None]) -> None:
        invoke_now = False
        with self._lock:
            if self._event.is_set():
                invoke_now = True
            else:
                self._callbacks.append(callback)
        if invoke_now:
            try:
                callback()
            except Exception:
                pass

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TaskCancelledError(self.reason)

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout=timeout)


class EmergencyStopController:
    """Global kill-switch halting all agent tasks, child subprocesses, and voice I/O."""

    def __init__(self) -> None:
        self._engaged = threading.Event()
        self._lock = threading.RLock()
        self._reason: str = ""
        self._engaged_at: Optional[float] = None
        self._active_tokens: Set[CancellationToken] = set()
        self._tracked_processes: Set[subprocess.Popen] = set()
        self._hooks: List[Callable[[str], None]] = []

    @property
    def is_engaged(self) -> bool:
        return self._engaged.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def register_hook(self, hook: Callable[[str], None]) -> None:
        with self._lock:
            self._hooks.append(hook)

    def create_task_token(self) -> CancellationToken:
        with self._lock:
            if self._engaged.is_set():
                raise EmergencyStopError(
                    f"Emergency Stop is active ({self._reason}). Reset before running new tasks."
                )
            token = CancellationToken()
            self._active_tokens.add(token)
            return token

    def release_task_token(self, token: CancellationToken) -> None:
        with self._lock:
            self._active_tokens.discard(token)

    def cancel_all_tasks(self, reason: str = "Task cancelled by user.") -> int:
        with self._lock:
            tokens = list(self._active_tokens)
            procs = list(self._tracked_processes)
        for token in tokens:
            token.cancel(reason)
        self._terminate_processes(procs)
        return len(tokens)

    def track_process(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if self._engaged.is_set():
                self._terminate_processes([proc])
                raise EmergencyStopError("Emergency Stop active — blocked subprocess start.")
            self._tracked_processes.add(proc)

    def untrack_process(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._tracked_processes.discard(proc)

    def _terminate_processes(self, procs: List[subprocess.Popen]) -> int:
        killed = 0
        for proc in procs:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    killed += 1
            except Exception:
                pass
        return killed

    def trigger(self, reason: str = "Emergency Stop activated by user.") -> dict:
        """Immediately halt all running operations, processes, and speech."""
        with self._lock:
            self._reason = reason
            self._engaged_at = time.time()
            self._engaged.set()
            tokens = list(self._active_tokens)
            procs = list(self._tracked_processes)
            self._tracked_processes.clear()
            hooks = list(self._hooks)

        for token in tokens:
            token.cancel(f"EMERGENCY STOP: {reason}")
        killed_procs = self._terminate_processes(procs)
        for hook in hooks:
            try:
                hook(reason)
            except Exception:
                pass

        return {
            "engaged": True,
            "reason": reason,
            "cancelled_tasks": len(tokens),
            "terminated_processes": killed_procs,
        }

    def reset(self) -> None:
        """Disengage Emergency Stop so user can resume normal operation."""
        with self._lock:
            self._engaged.clear()
            self._reason = ""
            self._engaged_at = None

    def check(self, token: Optional[CancellationToken] = None) -> None:
        if self._engaged.is_set():
            raise EmergencyStopError(f"Emergency Stop is active: {self._reason}")
        if token is not None:
            token.raise_if_cancelled()
