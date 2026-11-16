"""Developer Workflow Specialist: VS Code, Blender projects, safe shell, Python runner & log debugger."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from jarvis.automation.app_controller import AppController
from jarvis.filesystem.document_reader import DocumentReader
from jarvis.filesystem.operations import FileSystemManager
from jarvis.security.emergency import CancellationToken, EmergencyStopController
from jarvis.system.monitor import SystemMonitor


DEV_PROCESS_KEYWORDS = {
    "python", "python3", "python.exe", "node", "node.exe", "npm", "pnpm", "yarn",
    "code", "code.exe", "blender", "blender.exe", "git", "git.exe", "docker",
    "cargo", "rustc", "go", "java", "javac", "dotnet", "ollama", "ollama.exe",
    "uvicorn", "gunicorn", "vite", "pytest",
}

ERROR_LINE_RE = re.compile(
    r"(?i)(\b(error|exception|traceback|fatal|critical|failed|failure|"
    r"cuda out of memory|segmentation fault)\b|[a-z0-9_]+(error|exception)\b)"
)

FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)')


class DeveloperAutomation:
    """Executes verified developer & Blender creator workflows."""

    def __init__(
        self,
        app_controller: AppController,
        fs_manager: FileSystemManager,
        doc_reader: DocumentReader,
        monitor: SystemMonitor,
        emergency: EmergencyStopController,
        workspace_dir: Path,
    ) -> None:
        self.apps = app_controller
        self.fs = fs_manager
        self.reader = doc_reader
        self.monitor = monitor
        self.emergency = emergency
        self.workspace_dir = workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def open_vscode(self, project_path: Optional[str] = None) -> Dict[str, Any]:
        """Launch Visual Studio Code, optionally opening a project folder or file."""
        return self.apps.open_application("code", target_path=project_path)

    def find_projects(
        self,
        query: str = "",
        project_type: str = "all",  # all | blender | python | node | git
        token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        """Discover software repositories and Blender (.blend) projects inside permitted folders."""
        ptype = (project_type or "all").strip().lower()
        results: List[Dict[str, Any]] = []

        # 1. Locate .blend files directly when searching Blender or all projects
        if ptype in {"all", "blender"}:
            # Strip generic words ("blender", "project", "file") so "Blender educational project" matches "educational_scene.blend"!
            cleaned_q = re.sub(r"(?i)\b(blender|project|projects|file|files|my)\b", "", query).strip()
            blend_hits = self.fs.search_files(
                query=cleaned_q,
                extension=".blend",
                item_type="file",
                max_results=30,
                token=token,
            )
            # If strict query found nothing, list all .blend projects so agent can pick the right match
            if blend_hits["count"] == 0 and cleaned_q:
                blend_hits = self.fs.search_files(
                    query="",
                    extension=".blend",
                    item_type="file",
                    max_results=30,
                    token=token,
                )
            for item in blend_hits.get("matches", []):
                results.append(
                    {
                        "name": item["name"],
                        "path": item["path"],
                        "project_dir": str(Path(item["path"]).parent),
                        "project_type": "blender",
                        "size_human": item.get("size_human", ""),
                        "modified": item.get("modified", ""),
                    }
                )

        # 2. Locate code project folders by marker files
        if ptype in {"all", "python", "node", "git"}:
            markers = {
                "pyproject.toml": "python",
                "requirements.txt": "python",
                "package.json": "node",
                "Cargo.toml": "rust",
                ".git": "git",
            }
            folder_hits = self.fs.search_files(
                query=re.sub(r"(?i)\b(project|projects|repo|repository|folder|my)\b", "", query).strip(),
                item_type="folder",
                max_results=50,
                token=token,
            )
            for item in folder_hits.get("matches", []):
                p = Path(item["path"])
                detected = [mtype for fname, mtype in markers.items() if (p / fname).exists()]
                if not detected:
                    continue
                if ptype != "all" and ptype not in detected:
                    continue
                results.append(
                    {
                        "name": item["name"],
                        "path": str(p),
                        "project_type": ",".join(sorted(set(detected))),
                    }
                )

        return {
            "success": True,
            "verified": True,
            "query": query,
            "project_type": ptype,
            "count": len(results),
            "projects": results[:40],
        }

    def open_and_check_blender_project(
        self,
        blend_path: str,
        launch_blender: bool = True,
    ) -> Dict[str, Any]:
        """Inspect a .blend project's health, check GPU VRAM headroom, and optionally launch Blender."""
        inspection = self.reader.inspect_blend_file(blend_path)
        if not inspection.get("success"):
            return inspection

        gpu = self.monitor.query_gpu()
        vram_note = None
        if gpu.available and gpu.vram_free_mb < 1500:
            vram_note = (
                f"Only {gpu.vram_free_mb} MB GPU VRAM is currently free on {gpu.name}. "
                "Consider switching Jarvis to a 3B model while rendering in Blender."
            )

        blender_entry = self.apps.registry.resolve("blender")
        launch_result: Dict[str, Any] = {"attempted": False}
        if launch_blender:
            if not inspection.get("healthy"):
                launch_result = {
                    "attempted": False,
                    "reason": "Skipped auto-launch because .blend header inspection detected corruption issues.",
                }
            elif blender_entry is None:
                launch_result = {
                    "attempted": True,
                    "success": False,
                    "verified": True,
                    "error": "Blender executable is not installed or not found on this machine.",
                }
            else:
                launch_result = {
                    "attempted": True,
                    **self.apps.open_application("blender", target_path=inspection["path"]),
                }

        overall_ok = bool(inspection.get("healthy")) and (
            not launch_blender or bool(launch_result.get("success"))
        )
        verified = bool(inspection.get("verified")) and (
            not launch_blender or bool(launch_result.get("verified"))
        )

        return {
            "success": overall_ok,
            "verified": verified,
            "blend_inspection": inspection,
            "blender_installed": blender_entry is not None,
            "blender_executable": blender_entry.executable if blender_entry else None,
            "launch_result": launch_result,
            "gpu_vram_advisory": vram_note,
        }

    def run_terminal_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_sec: float = 30.0,
        token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        """Execute a SecurityGate-approved terminal command without unsafe shell injection."""
        work_dir = (
            Path(os.path.expandvars(cwd)).expanduser().resolve()
            if cwd
            else self.workspace_dir
        )
        if not work_dir.is_dir():
            return {"success": False, "verified": True, "error": f"Working directory not found: {work_dir}"}

        timeout = max(2.0, min(float(timeout_sec), 120.0))
        started = time.perf_counter()

        # Windows built-ins (dir, echo, type, where) require cmd /c; normal binaries run arg-list directly!
        win_shell_builtins = {"dir", "echo", "type", "ver", "vol", "set"}
        try:
            argv = shlex.split(command, posix=(os.name != "nt"))
            if os.name == "nt":
                # shlex with posix=False preserves surrounding quotes; strip outer quotes for CreateProcess
                argv = [
                    tok[1:-1]
                    if (len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"))
                    else tok
                    for tok in argv
                ]
        except ValueError as exc:
            return {"success": False, "verified": True, "error": f"Malformed command syntax: {exc}"}

        if os.name == "nt" and argv and argv[0].lower() in win_shell_builtins:
            exec_cmd = ["cmd.exe", "/d", "/c", command]
        elif os.name == "nt" and argv and argv[0].lower().startswith("get-"):
            exec_cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            exec_cmd = argv

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                exec_cmd,
                cwd=str(work_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except (FileNotFoundError, OSError) as exc:
            return {
                "success": False,
                "verified": True,
                "error": f"Failed to run executable '{exec_cmd[0] if exec_cmd else ''}': {exc}",
            }

        self.emergency.track_process(proc)
        if token:
            token.on_cancel(lambda: proc.terminate() if proc.poll() is None else None)

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            return {
                "success": False,
                "verified": True,
                "exit_code": -1,
                "error": f"Command timed out after {timeout}s and was terminated.",
                "stdout": (stdout or "")[:4000],
                "stderr": (stderr or "")[:4000],
            }
        finally:
            self.emergency.untrack_process(proc)

        if token and token.is_cancelled:
            token.raise_if_cancelled()

        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return {
            "success": proc.returncode == 0,
            "verified": True,
            "command": command,
            "cwd": str(work_dir),
            "exit_code": proc.returncode,
            "duration_ms": elapsed_ms,
            "stdout": (stdout or "")[:6000],
            "stderr": (stderr or "")[:4000],
            "error": None if proc.returncode == 0 else f"Exited with code {proc.returncode}: {(stderr or stdout)[:300]}",
        }

    def run_python_script(
        self,
        script_path: str,
        args: Optional[List[str]] = None,
        timeout_sec: float = 30.0,
        token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        """Run a local Python script, verify exit code, and parse any traceback for debugging."""
        target = Path(os.path.expandvars(script_path)).expanduser().resolve()
        if not target.is_file():
            return {"success": False, "verified": True, "error": f"Python script not found: {target}"}
        if target.suffix.lower() != ".py":
            return {"success": False, "verified": True, "error": f"Expected a .py file, got '{target.name}'."}

        timeout = max(2.0, min(float(timeout_sec), 120.0))
        cmd = [sys.executable, str(target), *(str(a) for a in (args or []))]
        started = time.perf_counter()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

        proc = subprocess.Popen(
            cmd,
            cwd=str(target.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        self.emergency.track_process(proc)
        if token:
            token.on_cancel(lambda: proc.terminate() if proc.poll() is None else None)

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            return {
                "success": False,
                "verified": True,
                "script_path": str(target),
                "exit_code": -1,
                "error": f"Script execution timed out after {timeout}s.",
                "stdout": (stdout or "")[:4000],
                "stderr": (stderr or "")[:4000],
            }
        finally:
            self.emergency.untrack_process(proc)

        if token and token.is_cancelled:
            token.raise_if_cancelled()

        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        traceback_info = self._extract_traceback_hints(stderr or "") if proc.returncode != 0 else None
        return {
            "success": proc.returncode == 0,
            "verified": True,
            "script_path": str(target),
            "exit_code": proc.returncode,
            "duration_ms": elapsed_ms,
            "stdout": (stdout or "")[:6000],
            "stderr": (stderr or "")[:4000],
            "traceback_summary": traceback_info,
            "error": None if proc.returncode == 0 else f"Script failed (exit {proc.returncode}): {traceback_info or stderr[:300]}",
        }

    @staticmethod
    def _extract_traceback_hints(stderr: str) -> Optional[Dict[str, Any]]:
        if not stderr.strip():
            return None
        lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
        frames = [{"file": m.group(1), "line": int(m.group(2))} for m in FILE_LINE_RE.finditer(stderr)]
        return {
            "exception_line": lines[-1] if lines else "",
            "frames": frames[-5:],
        }

    def list_dev_processes(self) -> Dict[str, Any]:
        """Monitor active developer tools, servers, interpreters, and Blender/Ollama processes."""
        items: List[Dict[str, Any]] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "cmdline"]):
            try:
                name = str(p.info.get("name") or "")
                low = name.lower()
                stem = low[:-4] if low.endswith(".exe") else low
                if low in DEV_PROCESS_KEYWORDS or stem in DEV_PROCESS_KEYWORDS:
                    mem = p.info.get("memory_info")
                    cmdline = " ".join((p.info.get("cmdline") or [])[:4])[:160]
                    items.append(
                        {
                            "pid": p.info["pid"],
                            "name": name,
                            "cpu_percent": round(float(p.info.get("cpu_percent") or 0.0), 1),
                            "memory_mb": round(mem.rss / (1024 * 1024), 1) if mem else 0.0,
                            "command_preview": cmdline,
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        items.sort(key=lambda x: x["memory_mb"], reverse=True)
        return {
            "success": True,
            "verified": True,
            "count": len(items),
            "processes": items[:30],
        }

    def analyze_error_log(self, log_path: str, tail_lines: int = 300) -> Dict[str, Any]:
        """Read a log file and extract errors, exceptions, stack frames & line numbers."""
        target = Path(os.path.expandvars(log_path)).expanduser().resolve()
        if not target.is_file():
            return {"success": False, "verified": True, "error": f"Log file not found: {target}"}

        text = target.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        start_idx = max(0, len(all_lines) - tail_lines)
        window = all_lines[start_idx:]

        error_entries: List[Dict[str, Any]] = []
        for offset, line in enumerate(window):
            if ERROR_LINE_RE.search(line):
                error_entries.append(
                    {"line_number": start_idx + offset + 1, "text": line.strip()[:300]}
                )

        frames = [{"file": m.group(1), "line": int(m.group(2))} for m in FILE_LINE_RE.finditer("\n".join(window))]
        return {
            "success": True,
            "verified": True,
            "log_path": str(target),
            "total_lines": len(all_lines),
            "inspected_lines": len(window),
            "error_count": len(error_entries),
            "recent_errors": error_entries[-25:],
            "referenced_source_locations": frames[-10:],
        }
