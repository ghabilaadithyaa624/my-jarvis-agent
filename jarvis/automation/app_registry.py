"""Windows 11 Application Discovery & Natural-Language Resolver.

Automatically locates executables across PATH, standard Windows 11 Program Files,
AppData, Windows Registry App Paths, and Start Menu shortcuts.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class AppEntry:
    canonical_name: str
    display_name: str
    executable: str               # Full path, PATH command, or URI protocol
    process_names: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    is_uri: bool = False


class AppRegistry:
    """Resolves natural-language app requests ('open VS Code', 'launch Blender') to verified binaries."""

    def __init__(self) -> None:
        self._apps: Dict[str, AppEntry] = {}
        self._alias_index: Dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        self._apps.clear()
        self._alias_index.clear()
        self._register_known_apps()
        if os.name == "nt":
            self._scan_windows_registry_app_paths()
            self._scan_windows_start_menu()

    def _add(self, entry: AppEntry) -> None:
        key = entry.canonical_name.lower()
        self._apps[key] = entry
        for alias in [key, entry.display_name.lower(), *entry.aliases]:
            a = alias.strip().lower()
            if a:
                self._alias_index[a] = key

    @staticmethod
    def _first_existing(paths: List[Path], which_names: List[str]) -> Optional[str]:
        for wn in which_names:
            found = shutil.which(wn)
            if found:
                return found
        for p in paths:
            if p.is_file():
                return str(p)
        return None

    def _register_known_apps(self) -> None:
        local_app = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        prog = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        prog86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))

        # VS Code
        vscode_exe = self._first_existing(
            [
                local_app / "Programs/Microsoft VS Code/Code.exe",
                prog / "Microsoft VS Code/Code.exe",
                local_app / "Programs/Microsoft VS Code/bin/code.cmd",
            ],
            ["code", "code.cmd"],
        )
        if vscode_exe:
            self._add(AppEntry(
                "code", "Visual Studio Code", vscode_exe,
                process_names=["code.exe", "code"],
                aliases=["vscode", "vs code", "visual studio code", "code editor"],
            ))

        # Blender (detect newest installed Blender 3.x / 4.x / 5.x automatically!)
        blender_candidates: List[Path] = []
        for bf_root in (prog / "Blender Foundation", prog86 / "Blender Foundation"):
            if bf_root.is_dir():
                blender_candidates.extend(sorted(bf_root.glob("Blender*/blender.exe"), reverse=True))
        blender_exe = self._first_existing(blender_candidates, ["blender"])
        if blender_exe:
            self._add(AppEntry(
                "blender", "Blender", blender_exe,
                process_names=["blender.exe", "blender"],
                aliases=["blender 3d", "blender app"],
            ))

        # Browsers
        chrome_exe = self._first_existing(
            [
                prog / "Google/Chrome/Application/chrome.exe",
                prog86 / "Google/Chrome/Application/chrome.exe",
                local_app / "Google/Chrome/Application/chrome.exe",
            ],
            ["chrome", "google-chrome", "chromium"],
        )
        if chrome_exe:
            self._add(AppEntry(
                "chrome", "Google Chrome", chrome_exe,
                process_names=["chrome.exe", "chrome", "chromium"],
                aliases=["google chrome", "chrome browser"],
            ))

        edge_exe = self._first_existing(
            [
                prog86 / "Microsoft/Edge/Application/msedge.exe",
                prog / "Microsoft/Edge/Application/msedge.exe",
            ],
            ["msedge", "microsoft-edge"],
        )
        if edge_exe:
            self._add(AppEntry(
                "msedge", "Microsoft Edge", edge_exe,
                process_names=["msedge.exe", "msedge"],
                aliases=["edge", "microsoft edge", "edge browser"],
            ))

        firefox_exe = self._first_existing(
            [prog / "Mozilla Firefox/firefox.exe", prog86 / "Mozilla Firefox/firefox.exe"],
            ["firefox"],
        )
        if firefox_exe:
            self._add(AppEntry(
                "firefox", "Mozilla Firefox", firefox_exe,
                process_names=["firefox.exe", "firefox"],
                aliases=["mozilla firefox"],
            ))

        brave_exe = self._first_existing(
            [
                prog / "BraveSoftware/Brave-Browser/Application/brave.exe",
                local_app / "BraveSoftware/Brave-Browser/Application/brave.exe",
            ],
            ["brave", "brave-browser"],
        )
        if brave_exe:
            self._add(AppEntry(
                "brave", "Brave Browser", brave_exe,
                process_names=["brave.exe", "brave"],
                aliases=["brave browser"],
            ))

        # Standard Windows 11 built-ins
        if os.name == "nt":
            builtins = [
                AppEntry("notepad", "Notepad", "notepad.exe", ["notepad.exe"], ["text editor"]),
                AppEntry("calc", "Calculator", "calc.exe", ["calculatorapp.exe", "calc.exe"], ["calculator"]),
                AppEntry("explorer", "File Explorer", "explorer.exe", ["explorer.exe"], ["file explorer", "windows explorer", "my computer", "this pc"]),
                AppEntry("mspaint", "Paint", "mspaint.exe", ["mspaint.exe"], ["paint", "ms paint"]),
                AppEntry("wt", "Windows Terminal", shutil.which("wt.exe") or "cmd.exe", ["windowsterminal.exe", "wt.exe", "cmd.exe"], ["terminal", "windows terminal"]),
                AppEntry("powershell", "PowerShell", shutil.which("pwsh.exe") or "powershell.exe", ["pwsh.exe", "powershell.exe"], ["pwsh", "windows powershell"]),
                AppEntry("cmd", "Command Prompt", "cmd.exe", ["cmd.exe"], ["command prompt"]),
                AppEntry("taskmgr", "Task Manager", "taskmgr.exe", ["taskmgr.exe"], ["task manager"]),
                AppEntry("snippingtool", "Snipping Tool", "snippingtool.exe", ["snippingtool.exe"], ["snipping tool"]),
            ]
            for b in builtins:
                self._add(b)

        # Common desktop tools
        extra_specs = [
            ("vlc", "VLC Media Player", [prog / "VideoLAN/VLC/vlc.exe", prog86 / "VideoLAN/VLC/vlc.exe"], ["vlc"], ["vlc.exe", "vlc"], ["vlc player", "media player"]),
            ("obs", "OBS Studio", [prog / "obs-studio/bin/64bit/obs64.exe"], ["obs64", "obs"], ["obs64.exe", "obs"], ["obs studio"]),
            ("spotify", "Spotify", [Path(os.environ.get("APPDATA", "")) / "Spotify/Spotify.exe"], ["spotify"], ["spotify.exe", "spotify"], ["spotify music"]),
            ("discord", "Discord", [local_app / "Discord/Update.exe"], ["discord"], ["discord.exe", "discord"], ["discord app"]),
            ("python", "Python", [], ["python", "python3"], ["python.exe", "python3", "python"], ["python3"]),
        ]
        for canon, disp, paths, wnames, pnames, aliases in extra_specs:
            exe = self._first_existing(paths, wnames)
            if exe:
                self._add(AppEntry(canon, disp, exe, pnames, aliases))

    def _scan_windows_registry_app_paths(self) -> None:
        """Discover installed Windows desktop apps registered under App Paths."""
        try:
            import winreg

            roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
            subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
            for hive in roots:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        count = winreg.QueryInfoKey(key)[0]
                        for idx in range(min(count, 300)):
                            exe_name = winreg.EnumKey(key, idx)
                            stem = Path(exe_name).stem.lower()
                            if stem in self._apps:
                                continue
                            try:
                                with winreg.OpenKey(key, exe_name) as app_key:
                                    val, _ = winreg.QueryValueEx(app_key, "")
                                    exe_path = os.path.expandvars(str(val)).strip('"')
                                    if os.path.isfile(exe_path):
                                        self._add(AppEntry(
                                            canonical_name=stem,
                                            display_name=stem.title(),
                                            executable=exe_path,
                                            process_names=[exe_name.lower()],
                                        ))
                            except OSError:
                                continue
                except OSError:
                    continue
        except Exception:
            pass

    def _scan_windows_start_menu(self) -> None:
        """Index Start Menu .lnk shortcuts for natural-language app launching."""
        start_dirs = [
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
        ]
        for sdir in start_dirs:
            if not sdir.is_dir():
                continue
            for lnk in sdir.rglob("*.lnk"):
                label = lnk.stem.strip()
                low = label.lower()
                if any(skip in low for skip in ("uninstall", "readme", "help", "website", "license")):
                    continue
                if low not in self._alias_index:
                    self._add(AppEntry(
                        canonical_name=low,
                        display_name=label,
                        executable=str(lnk),
                        process_names=[f"{low.replace(' ', '')}.exe"],
                    ))

    def resolve(self, name: str) -> Optional[AppEntry]:
        q = (name or "").strip().lower()
        if not q:
            return None
        if q.endswith(".exe"):
            q = q[:-4]
        if q in self._alias_index:
            return self._apps[self._alias_index[q]]
        # Substring fallback across aliases
        for alias, key in self._alias_index.items():
            if q in alias or alias in q:
                return self._apps[key]
        # Direct PATH lookup fallback
        which_hit = shutil.which(q) or (shutil.which(f"{q}.exe") if os.name == "nt" else None)
        if which_hit:
            proc = Path(which_hit).name.lower()
            return AppEntry(q, q.title(), which_hit, process_names=[proc])
        return None

    def list_apps(self) -> List[Dict[str, str]]:
        return [
            {
                "name": e.canonical_name,
                "display_name": e.display_name,
                "executable": e.executable,
            }
            for e in sorted(self._apps.values(), key=lambda x: x.display_name.lower())
        ]
