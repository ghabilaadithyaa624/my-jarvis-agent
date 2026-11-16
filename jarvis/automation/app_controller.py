"""Windows 11 Application Controller: launch, focus switch, detect running & close.

Every operation verifies actual OS process / window state before reporting success.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from jarvis.automation.app_registry import AppEntry, AppRegistry
from jarvis.logs.logger import get_logger

log = get_logger("automation.apps")


class AppController:
    """Manages desktop application lifecycle on Windows 11."""

    def __init__(self, registry: Optional[AppRegistry] = None) -> None:
        self.registry = registry or AppRegistry()

    def is_running(self, app_name: str) -> Dict[str, Any]:
        """Check whether an application is currently running and return matching PIDs."""
        entry = self.registry.resolve(app_name)
        q = app_name.strip().lower()
        candidate_names = set()
        if entry:
            candidate_names.update(n.lower() for n in entry.process_names)
            candidate_names.add(entry.canonical_name.lower())
            candidate_names.add(f"{entry.canonical_name.lower()}.exe")
        candidate_names.add(q)
        if not q.endswith(".exe"):
            candidate_names.add(f"{q}.exe")

        instances: List[Dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "memory_info"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                pstem = pname[:-4] if pname.endswith(".exe") else pname
                exe_raw = proc.info.get("exe") or ""
                exe_name = Path(exe_raw).name.lower() if exe_raw else ""
                exe_stem = exe_name[:-4] if exe_name.endswith(".exe") else exe_name
                # Also strip trailing version suffix like python3.13 -> python3
                exe_base = exe_stem.split(".")[0] if exe_stem else ""
                matched = bool(
                    {pname, pstem, exe_name, exe_stem, exe_base} & candidate_names
                )
                if matched:
                    mem = proc.info.get("memory_info")
                    instances.append(
                        {
                            "pid": proc.info["pid"],
                            "name": proc.info.get("name"),
                            "memory_mb": round(mem.rss / (1024 * 1024), 1) if mem else 0.0,
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return {
            "success": True,
            "verified": True,
            "app_query": app_name,
            "resolved_app": entry.display_name if entry else app_name,
            "running": len(instances) > 0,
            "instance_count": len(instances),
            "instances": instances[:15],
        }

    def open_application(
        self,
        app_name: str,
        target_path: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Launch a desktop application (optionally with a file/project) and verify process start."""
        entry = self.registry.resolve(app_name)
        if entry is None:
            return {
                "success": False,
                "verified": True,
                "error": f"Application '{app_name}' was not found on this system. Check installation or add its path.",
            }

        args_list: List[str] = list(extra_args or [])
        if target_path:
            resolved_target = Path(os.path.expandvars(target_path)).expanduser().resolve()
            if not resolved_target.exists():
                return {
                    "success": False,
                    "verified": True,
                    "error": f"Project/file path does not exist: {resolved_target}",
                }
            args_list.append(str(resolved_target))

        try:
            pid = self._spawn(entry, args_list)
        except Exception as exc:
            return {
                "success": False,
                "verified": False,
                "app": entry.display_name,
                "executable": entry.executable,
                "error": f"Failed launching '{entry.display_name}': {exc}",
            }

        # Verify the spawned PID is alive OR the target app process appears within 3.5s
        verified = False
        active_pids: List[int] = []
        deadline = time.time() + 3.5
        while time.time() < deadline:
            if pid and psutil.pid_exists(pid):
                verified = True
                active_pids = [pid]
                break
            status = self.is_running(entry.canonical_name)
            if status["running"]:
                verified = True
                active_pids = [i["pid"] for i in status["instances"]]
                break
            time.sleep(0.2)

        return {
            "success": verified,
            "verified": verified,
            "app": entry.display_name,
            "canonical_name": entry.canonical_name,
            "executable": entry.executable,
            "target_path": target_path,
            "pids": active_pids,
            "error": None if verified else f"Launched '{entry.display_name}', but could not verify a running process.",
        }

    @staticmethod
    def _spawn(entry: AppEntry, args_list: List[str]) -> Optional[int]:
        exe = entry.executable
        if os.name == "nt" and exe.lower().endswith(".lnk") and not args_list:
            os.startfile(exe)  # type: ignore[attr-defined]
            return None

        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )

        proc = subprocess.Popen(
            [exe, *args_list],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=(os.name != "nt"),
        )
        return proc.pid

    def switch_to_application(self, app_name: str) -> Dict[str, Any]:
        """Bring a running Windows 11 application window to foreground and verify focus."""
        if os.name != "nt":
            return {
                "success": False,
                "verified": False,
                "error": "Native Win32 window focus switching requires Windows 11.",
            }

        running_info = self.is_running(app_name)
        target_pids = {i["pid"] for i in running_info.get("instances", [])}
        q_low = app_name.strip().lower()

        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            matched_hwnd = None
            matched_title = ""

            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _enum_cb(hwnd, _lparam):
                nonlocal matched_hwnd, matched_title
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in target_pids or q_low in title.lower():
                    matched_hwnd = hwnd
                    matched_title = title
                    return False
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
            if not matched_hwnd:
                return {
                    "success": False,
                    "verified": True,
                    "error": f"No visible window found matching '{app_name}'.",
                }

            SW_RESTORE = 9
            if user32.IsIconic(matched_hwnd):
                user32.ShowWindow(matched_hwnd, SW_RESTORE)
            user32.SetForegroundWindow(matched_hwnd)
            time.sleep(0.15)
            fg = user32.GetForegroundWindow()
            verified = fg == matched_hwnd
            return {
                "success": True,
                "verified": verified,
                "window_title": matched_title,
                "note": None if verified else "Requested foreground focus (Windows focus lock may have flashed taskbar icon).",
            }
        except Exception as exc:
            return {"success": False, "verified": False, "error": str(exc)}

    def close_application(self, app_name: str, force: bool = False) -> Dict[str, Any]:
        """Gracefully close a user application and verify all matching PIDs exited."""
        entry = self.registry.resolve(app_name)
        canonical = entry.canonical_name if entry else app_name.strip().lower()
        if canonical in {"explorer"}:
            return {
                "success": False,
                "verified": True,
                "error": "Refusing to terminate Windows Explorer desktop shell.",
            }

        status = self.is_running(app_name)
        if not status["running"]:
            return {
                "success": False,
                "verified": True,
                "error": f"'{status['resolved_app']}' is not currently running.",
            }

        closed_pids: List[int] = []
        still_running: List[int] = []
        for inst in status["instances"]:
            pid = inst["pid"]
            if pid == os.getpid():
                continue
            try:
                proc = psutil.Process(pid)
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                    closed_pids.append(pid)
                except psutil.TimeoutExpired:
                    still_running.append(pid)
            except psutil.NoSuchProcess:
                closed_pids.append(pid)
            except psutil.AccessDenied:
                still_running.append(pid)

        verified = len(closed_pids) > 0 and len(still_running) == 0
        return {
            "success": len(closed_pids) > 0,
            "verified": verified,
            "app": status["resolved_app"],
            "closed_pids": closed_pids,
            "still_running_pids": still_running,
        }
