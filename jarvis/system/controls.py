"""Windows 11 system controls: audio volume, screenshots, ms-settings & safe process control.

Every action verifies its outcome and degrades cleanly with explicit status messages
when run outside Windows 11 or when optional native packages are absent.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

from jarvis.logs.logger import get_logger

log = get_logger("system.controls")

# Critical Windows, Linux, and JARVIS core processes that must NEVER be terminated
PROTECTED_PROCESS_NAMES = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "lsaiso.exe",
    "svchost.exe",
    "winlogon.exe",
    "dwm.exe",
    "explorer.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "taskhostw.exe",
    "ctfmon.exe",
    "conhost.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "msmpeng.exe",
    "nissrv.exe",
    "securityhealthservice.exe",
    "securityhealthsystray.exe",
    "nvcontainer.exe",
    "nvdisplay.container.exe",
    "wlanext.exe",
    "wudfhost.exe",
    "dllhost.exe",
    "ollama.exe",
    "ollama",
    "ollama app.exe",
    "ollama_llama_server.exe",
    "init",
    "systemd",
    "sshd",
    "bash",
}

# Known safe-to-stop bloat/updater/idle background helpers on Windows 11
KNOWN_BACKGROUND_BLOAT = {
    "onedrive.exe",
    "cortana.exe",
    "yourphone.exe",
    "phoneexperiencehost.exe",
    "gamebar.exe",
    "gamebarftserver.exe",
    "xboxpcapp.exe",
    "gamingservices.exe",
    "widgets.exe",
    "widgetservice.exe",
    "teams.exe",
    "ms-teams.exe",
    "skypeapp.exe",
    "spotify.exe",
    "discord.exe",
    "steam.exe",
    "steamwebhelper.exe",
    "epicgameslauncher.exe",
    "adobeupdater.exe",
    "acrotray.exe",
    "ccxprocess.exe",
    "creative cloud.exe",
    "ituneshelper.exe",
    "jusched.exe",
    "googleupdate.exe",
    "microsoftedgeupdate.exe",
    "braveupdate.exe",
}

# Windows 11 ms-settings: URI catalog
WINDOWS_SETTINGS_URIS: Dict[str, str] = {
    "settings": "ms-settings:",
    "home": "ms-settings:",
    "system": "ms-settings:display",
    "display": "ms-settings:display",
    "brightness": "ms-settings:display",
    "night light": "ms-settings:nightlight",
    "sound": "ms-settings:sound",
    "audio": "ms-settings:sound",
    "volume": "ms-settings:apps-volume",
    "microphone": "ms-settings:privacy-microphone",
    "notifications": "ms-settings:notifications",
    "power": "ms-settings:powersleep",
    "battery": "ms-settings:batterysaver",
    "storage": "ms-settings:storagesense",
    "disk": "ms-settings:storagesense",
    "multitasking": "ms-settings:multitasking",
    "about": "ms-settings:about",
    "bluetooth": "ms-settings:bluetooth",
    "devices": "ms-settings:connecteddevices",
    "printers": "ms-settings:printers",
    "mouse": "ms-settings:mousetouchpad",
    "touchpad": "ms-settings:devices-touchpad",
    "keyboard": "ms-settings:keyboard",
    "network": "ms-settings:network-status",
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "ethernet": "ms-settings:network-ethernet",
    "vpn": "ms-settings:network-vpn",
    "proxy": "ms-settings:network-proxy",
    "personalization": "ms-settings:personalization",
    "background": "ms-settings:personalization-background",
    "colors": "ms-settings:colors",
    "themes": "ms-settings:themes",
    "taskbar": "ms-settings:taskbar",
    "apps": "ms-settings:appsfeatures",
    "installed apps": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps",
    "startup": "ms-settings:startupapps",
    "startup apps": "ms-settings:startupapps",
    "accounts": "ms-settings:yourinfo",
    "time": "ms-settings:dateandtime",
    "date": "ms-settings:dateandtime",
    "language": "ms-settings:regionlanguage",
    "speech": "ms-settings:speech",
    "gaming": "ms-settings:gaming-gamebar",
    "graphics": "ms-settings:display-advancedgraphics",
    "gpu": "ms-settings:display-advancedgraphics",
    "accessibility": "ms-settings:easeofaccess",
    "privacy": "ms-settings:privacy",
    "camera": "ms-settings:privacy-webcam",
    "security": "ms-settings:windowsdefender",
    "defender": "ms-settings:windowsdefender",
    "update": "ms-settings:windowsupdate",
    "windows update": "ms-settings:windowsupdate",
    "developers": "ms-settings:developers",
}


class SystemController:
    """Executes verified Windows 11 system control commands."""

    def __init__(self, screenshots_dir: Path) -> None:
        self.screenshots_dir = screenshots_dir
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._self_pid = os.getpid()

    # ---------------- Volume Control ----------------

    def _get_pycaw_endpoint(self):
        """Acquire Windows WASAPI IAudioEndpointVolume via pycaw if available."""
        if os.name != "nt":
            return None
        from ctypes import POINTER, cast

        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        comtypes.CoInitialize()
        devices = AudioUtilities.GetSpeakers()
        # Handle both classic pycaw and newer pycaw EndpointVolume property
        if hasattr(devices, "EndpointVolume"):
            return devices.EndpointVolume
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def get_volume(self) -> Dict[str, Any]:
        """Read current Windows master audio volume (0-100) and mute state."""
        if os.name != "nt":
            return {
                "supported": False,
                "error": "Native Windows WASAPI volume control requires Windows 11.",
            }
        try:
            endpoint = self._get_pycaw_endpoint()
            if endpoint is None:
                raise RuntimeError("No audio endpoint found.")
            scalar = float(endpoint.GetMasterVolumeLevelScalar())
            muted = bool(endpoint.GetMute())
            return {
                "supported": True,
                "volume_percent": int(round(scalar * 100)),
                "muted": muted,
                "verified": True,
            }
        except ImportError:
            return {
                "supported": False,
                "error": "Install 'pycaw' and 'comtypes' for exact Windows volume level control (pip install pycaw comtypes).",
            }
        except Exception as exc:
            return {"supported": False, "error": f"Failed reading Windows volume: {exc}"}

    def set_volume(
        self,
        level: Optional[int] = None,
        mute: Optional[bool] = None,
        delta: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Adjust Windows 11 master volume (0-100), relative delta, or mute state & verify."""
        if os.name != "nt":
            return {
                "success": False,
                "verified": False,
                "error": "Master volume control requires Windows 11 (WASAPI).",
            }
        try:
            endpoint = self._get_pycaw_endpoint()
            if endpoint is None:
                raise RuntimeError("No audio output device detected.")

            current_pct = int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
            if delta is not None and level is None:
                level = current_pct + int(delta)
            if level is not None:
                clamped = max(0, min(100, int(level)))
                endpoint.SetMasterVolumeLevelScalar(clamped / 100.0, None)
            if mute is not None:
                endpoint.SetMute(1 if mute else 0, None)

            # Read back hardware level to verify!
            actual_pct = int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
            actual_muted = bool(endpoint.GetMute())
            verified = True
            if level is not None and abs(actual_pct - max(0, min(100, int(level)))) > 2:
                verified = False
            if mute is not None and actual_muted != bool(mute):
                verified = False

            return {
                "success": True,
                "verified": verified,
                "previous_percent": current_pct,
                "volume_percent": actual_pct,
                "muted": actual_muted,
            }
        except ImportError:
            return {
                "success": False,
                "verified": False,
                "error": "pycaw is not installed. Run: pip install pycaw comtypes",
            }
        except Exception as exc:
            return {"success": False, "verified": False, "error": str(exc)}

    # ---------------- Screenshot Capture ----------------

    def take_screenshot(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Capture desktop screenshot, save to permitted path, and verify image file."""
        if output_path:
            dest = Path(output_path).expanduser().resolve()
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = (self.screenshots_dir / f"screenshot_{stamp}.png").resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Never silently overwrite an existing screenshot
        if dest.exists():
            dest = dest.with_stem(f"{dest.stem}_{int(time.time())}")

        errors: List[str] = []

        # Method 1: mss (fastest multi-monitor support on Windows 11)
        try:
            import mss
            import mss.tools

            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(monitor)
                mss.tools.to_png(shot.rgb, shot.size, output=str(dest))
            if dest.is_file() and dest.stat().st_size > 0:
                return {
                    "success": True,
                    "verified": True,
                    "path": str(dest),
                    "width": shot.width,
                    "height": shot.height,
                    "size_bytes": dest.stat().st_size,
                    "method": "mss",
                }
        except Exception as exc:
            errors.append(f"mss: {exc}")

        # Method 2: Pillow ImageGrab (built-in on Windows/macOS)
        try:
            from PIL import ImageGrab

            img = ImageGrab.grab()
            img.save(str(dest), format="PNG")
            if dest.is_file() and dest.stat().st_size > 0:
                return {
                    "success": True,
                    "verified": True,
                    "path": str(dest),
                    "width": img.width,
                    "height": img.height,
                    "size_bytes": dest.stat().st_size,
                    "method": "PIL.ImageGrab",
                }
        except Exception as exc:
            errors.append(f"PIL.ImageGrab: {exc}")

        # Method 3: Windows PowerShell System.Drawing fallback (zero pip dependencies!)
        if os.name == "nt":
            try:
                safe_out = str(dest).replace("'", "''")
                ps_script = (
                    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                    "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                    "$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height); "
                    "$g = [System.Drawing.Graphics]::FromImage($bmp); "
                    "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
                    f"$bmp.Save('{safe_out}', [System.Drawing.Imaging.ImageFormat]::Png); "
                    "$g.Dispose(); $bmp.Dispose()"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                    check=True,
                    capture_output=True,
                    timeout=10.0,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if dest.is_file() and dest.stat().st_size > 0:
                    return {
                        "success": True,
                        "verified": True,
                        "path": str(dest),
                        "size_bytes": dest.stat().st_size,
                        "method": "powershell_dotnet",
                    }
            except Exception as exc:
                errors.append(f"powershell: {exc}")

        return {
            "success": False,
            "verified": False,
            "error": "Could not capture screenshot (no active display or capture library). " + "; ".join(errors),
        }

    # ---------------- Windows 11 Settings ----------------

    def resolve_settings_uri(self, page: str) -> Tuple[str, str]:
        """Resolve a natural-language Windows Settings page name to an ms-settings URI."""
        key = (page or "home").strip().lower()
        for prefix in ("windows ", "open ", "settings "):
            if key.startswith(prefix) and key[len(prefix):] in WINDOWS_SETTINGS_URIS:
                key = key[len(prefix):]
        if key in WINDOWS_SETTINGS_URIS:
            return key, WINDOWS_SETTINGS_URIS[key]
        for name, uri in WINDOWS_SETTINGS_URIS.items():
            if key in name or name in key:
                return name, uri
        return "home", "ms-settings:"

    def open_windows_settings(self, page: str = "home") -> Dict[str, Any]:
        """Launch a specific Windows 11 Settings page and verify SystemSettings.exe runs."""
        matched_name, uri = self.resolve_settings_uri(page)
        if os.name != "nt":
            return {
                "success": False,
                "verified": False,
                "matched_page": matched_name,
                "uri": uri,
                "error": f"Opening '{uri}' requires Windows 11.",
            }
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
            # Wait briefly and verify SystemSettings.exe is running
            deadline = time.time() + 3.0
            running = False
            while time.time() < deadline:
                for p in psutil.process_iter(["name"]):
                    try:
                        if (p.info.get("name") or "").lower() == "systemsettings.exe":
                            running = True
                            break
                    except Exception:
                        continue
                if running:
                    break
                time.sleep(0.2)
            return {
                "success": True,
                "verified": running,
                "matched_page": matched_name,
                "uri": uri,
            }
        except Exception as exc:
            return {"success": False, "verified": False, "uri": uri, "error": str(exc)}

    # ---------------- Safe Process Management ----------------

    def is_protected_process(self, proc_name: str, pid: int) -> Tuple[bool, str]:
        if pid in {0, 4, 1, self._self_pid}:
            return True, "Kernel or JARVIS self process"
        try:
            parent = psutil.Process(self._self_pid).ppid()
            if pid == parent:
                return True, "JARVIS parent host process"
        except Exception:
            pass
        low = (proc_name or "").strip().lower()
        if low in PROTECTED_PROCESS_NAMES:
            return True, f"Protected OS/runtime process '{proc_name}'"
        return False, ""

    def find_unnecessary_processes(self, min_memory_mb: float = 50.0) -> Dict[str, Any]:
        """Identify safe, non-essential background processes consuming RAM/CPU.

        Never terminates anything automatically — returns candidates for user confirmation.
        """
        candidates: List[Dict[str, Any]] = []
        protected_count = 0
        current_user = ""
        try:
            current_user = psutil.Process(self._self_pid).username()
        except Exception:
            pass

        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                pid = int(info["pid"])
                name = str(info.get("name") or "")
                is_prot, _ = self.is_protected_process(name, pid)
                if is_prot:
                    protected_count += 1
                    continue

                # Only consider user-owned processes, never SYSTEM/root daemons
                p_user = str(info.get("username") or "")
                if current_user and p_user and p_user != current_user:
                    continue

                mem = info.get("memory_info")
                mem_mb = round((mem.rss / (1024 * 1024)), 1) if mem else 0.0
                cpu_pct = round(float(info.get("cpu_percent") or 0.0), 1)
                low_name = name.lower()

                is_known_bg = low_name in KNOWN_BACKGROUND_BLOAT or low_name.endswith("update.exe") or low_name.endswith("updater.exe")
                if is_known_bg or mem_mb >= min_memory_mb:
                    category = "known_background_app" if is_known_bg else "high_memory_user_app"
                    candidates.append(
                        {
                            "pid": pid,
                            "name": name,
                            "memory_mb": mem_mb,
                            "cpu_percent": cpu_pct,
                            "category": category,
                            "safe_to_request_stop": is_known_bg,
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        candidates.sort(key=lambda c: (not c["safe_to_request_stop"], -c["memory_mb"]))
        reclaimable_mb = round(
            sum(c["memory_mb"] for c in candidates if c["safe_to_request_stop"]), 1
        )
        return {
            "candidates": candidates[:25],
            "known_background_count": sum(1 for c in candidates if c["safe_to_request_stop"]),
            "estimated_reclaimable_mb": reclaimable_mb,
            "protected_processes_skipped": protected_count,
        }

    def stop_process(self, name_or_pid: str) -> Dict[str, Any]:
        """Gracefully terminate a permitted user process and verify PID exit."""
        raw = str(name_or_pid).strip()
        if not raw:
            return {"success": False, "verified": False, "error": "Empty process identifier."}

        targets: List[psutil.Process] = []
        if raw.isdigit():
            pid = int(raw)
            try:
                targets.append(psutil.Process(pid))
            except psutil.NoSuchProcess:
                return {"success": False, "verified": True, "error": f"No process with PID {pid} is running."}
        else:
            query = raw.lower()
            query_exe = query if query.endswith(".exe") else f"{query}.exe"
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    pname = (p.info.get("name") or "").lower()
                    if pname in {query, query_exe}:
                        targets.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if not targets:
                return {
                    "success": False,
                    "verified": True,
                    "error": f"No running process matching '{raw}' was found.",
                }

        stopped: List[Dict[str, Any]] = []
        blocked: List[str] = []
        errors: List[str] = []

        for proc in targets:
            try:
                pname = proc.name()
                is_prot, reason = self.is_protected_process(pname, proc.pid)
                if is_prot:
                    blocked.append(f"{pname} (PID {proc.pid}): {reason}")
                    continue
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except psutil.TimeoutExpired:
                    errors.append(f"{pname} (PID {proc.pid}) did not exit within 3s after graceful terminate.")
                    continue
                if not psutil.pid_exists(proc.pid):
                    stopped.append({"pid": proc.pid, "name": pname})
            except psutil.NoSuchProcess:
                stopped.append({"pid": proc.pid, "name": raw})
            except psutil.AccessDenied:
                errors.append(f"Access denied terminating PID {proc.pid} (requires elevated privileges).")
            except Exception as exc:
                errors.append(f"PID {proc.pid}: {exc}")

        success = len(stopped) > 0 and not blocked
        verified = len(stopped) > 0 and all(not psutil.pid_exists(s["pid"]) for s in stopped)
        return {
            "success": success,
            "verified": verified,
            "stopped": stopped,
            "blocked": blocked,
            "errors": errors,
        }
