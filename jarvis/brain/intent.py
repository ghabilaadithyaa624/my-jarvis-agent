"""Hybrid Intent Classifier, Language Detector & Context-Aware Tool Category Router."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class IntentKind(str, Enum):
    QUESTION = "question"                # Pure Q&A, explanation, brainstorming (no computer action)
    SYSTEM_QUERY = "system_query"        # Inspect local system state, specs, performance, time, processes
    DIRECT_ACTION = "direct_action"      # Single-tool computer command (open app, set timer, take screenshot)
    MULTI_STEP_TASK = "multi_step_task"  # Compound plan-execute-verify task
    META_CONTROL = "meta_control"        # Cancel, stop, emergency stop, mute/unmute


@dataclass
class IntentAnalysis:
    kind: IntentKind
    requires_tools: bool
    categories: List[str]
    detected_language: str = "en"
    meta_command: Optional[str] = None
    fast_tool: Optional[str] = None
    fast_args: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""


_TAMIL_CHAR_RE = re.compile(r"[\u0B80-\u0BFF]")

_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "application": [
        "open", "launch", "start", "close", "quit", "exit", "switch to", "focus",
        "bring up", "is running", "running app", "notepad", "calculator", "calc",
        "explorer", "chrome", "edge", "firefox", "brave", "vlc", "obs", "spotify",
        "discord", "paint", "terminal", "powershell", "திற", "மூடு",
    ],
    "filesystem": [
        "file", "files", "folder", "folders", "directory", "search", "find", "locate",
        "read", "document", "pdf", "docx", "excel", "create file", "write file",
        "make folder", "new folder", "rename", "move", "copy", "delete", "remove",
        "organize", "clean up", "sort", "duplicate", "duplicates", "downloads",
        "desktop", "documents", "drive", "கோப்பு", "கோப்புறை", "தேடு",
    ],
    "system": [
        "volume", "sound", "mute speaker", "unmute speaker", "louder", "quieter",
        "screenshot", "screen capture", "specs", "specification", "hardware",
        "cpu", "ram", "memory usage", "gpu", "vram", "rtx", "temperature",
        "performance", "health", "diagnostic", "diagnostics", "disk", "storage",
        "free space", "settings", "bluetooth", "wifi", "wi-fi", "display", "battery",
        "process", "processes", "running on my computer", "background process",
        "task manager", "slow", "lagging",
    ],
    "browser": [
        "website", "web", "browser", "url", "http", ".com", ".org", ".io", ".ai",
        "google", "duckduckgo", "youtube", "github", "search the web", "search online",
        "look up online", "tab", "tabs", "click", "fill form", "navigate", "playwright",
    ],
    "developer": [
        "vs code", "vscode", "visual studio code", "code", "project", "repository",
        "repo", "git", "terminal command", "command", "run", "execute", "script",
        "python", ".py", "pytest", "npm", "node", "debug", "error log", "log",
        "traceback", "exception", "crash", "blender", ".blend", "3d", "render",
    ],
    "utilities": [
        "remind", "reminder", "note", "notes", "todo", "to-do", "task list",
        "timer", "countdown", "alarm", "time", "date", "day today", "task history",
        "what did you do", "நேரம்", "தேதி", "நினைவூட்டு",
    ],
    "memory": [
        "remember", "memorize", "save preference", "my preference", "recall",
        "do you remember", "what do you know about me", "my project", "நினைவில்",
    ],
}

_PURE_QUESTION_STARTERS = re.compile(
    r"(?i)^(what is|what are|who is|why does|why do|how does|how do|explain (?!what is currently running)|"
    r"define|tell me about|difference between|compare|can you explain|summarize the concept|"
    r"write a poem|tell a joke|translate|hi|hello|hey|good morning|good afternoon|good evening|"
    r"thanks|thank you|no problem|sure|sounds good|cool|great|awesome|ok|okay|yes please|வணக்கம்)\b"
)

_LOCAL_CONTEXT_MARKERS = re.compile(
    r"(?i)\b(my computer|my laptop|my pc|my system|my files|my folder|my downloads|my desktop|"
    r"my documents|my project|my blender|my notes|my todos|my to-do|my reminders|currently running|"
    r"right now|time is it|date today|disk space|cpu|ram|gpu|vram|volume|screenshot|installed|"
    r"open|close|launch|create|execute|run|organize|delete|rename|move|copy|find|search|check|stop)\b"
)

_COMPOUND_MARKERS = re.compile(
    r"(?i)\b(and then|then|after that|and check|and verify|and run|and execute|and open|"
    r"and organize|and summarize|and fix|and stop|stop all unnecessary|check whether everything)\b"
)


class IntentClassifier:
    """Fast deterministic intent analyzer and tool category selector."""

    def analyze(self, text: str, default_language: str = "en") -> IntentAnalysis:
        raw = (text or "").strip()
        low = raw.lower()
        lang = "ta" if _TAMIL_CHAR_RE.search(raw) else default_language

        # 1. Meta control commands (instant safety handling)
        if low in {"emergency stop", "kill switch", "abort everything", "halt all"}:
            return IntentAnalysis(
                IntentKind.META_CONTROL, False, [], lang,
                meta_command="emergency_stop", summary="Activate Emergency Stop"
            )
        if low in {"stop", "cancel", "cancel task", "abort", "நிறுத்து"}:
            return IntentAnalysis(
                IntentKind.META_CONTROL, False, [], lang,
                meta_command="cancel_task", summary="Cancel active task"
            )
        if low in {"mute", "mute voice", "be quiet", "silence"}:
            return IntentAnalysis(
                IntentKind.META_CONTROL, False, [], lang,
                meta_command="mute_tts", summary="Mute voice output"
            )
        if low in {"unmute", "unmute voice", "speak again"}:
            return IntentAnalysis(
                IntentKind.META_CONTROL, False, [], lang,
                meta_command="unmute_tts", summary="Unmute voice output"
            )

        # 2. Match relevant tool categories
        matched_cats: Set[str] = set()
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in low:
                    matched_cats.add(cat)
                    break

        # Ensure interdependent categories are bundled together for multi-step workflows
        if "developer" in matched_cats:
            matched_cats.update({"filesystem", "application", "memory"})
        if "blender" in low or ".blend" in low:
            matched_cats.update({"developer", "filesystem", "application", "system", "memory"})
        if "unnecessary" in low and "process" in low:
            matched_cats.add("system")

        # 3. Pure conversational / general knowledge questions
        if not matched_cats or (
            _PURE_QUESTION_STARTERS.match(low) and not _LOCAL_CONTEXT_MARKERS.search(low)
        ):
            return IntentAnalysis(
                kind=IntentKind.QUESTION,
                requires_tools=False,
                categories=[],
                detected_language=lang,
                summary="Answer conversational or knowledge question directly.",
            )

        cats_sorted = sorted(matched_cats)

        # 4. Safe deterministic fast-paths for unambiguous single-step commands
        fast = self._match_fast_path(raw, low)
        if fast and not _COMPOUND_MARKERS.search(low):
            tool_name, tool_args, kind = fast
            return IntentAnalysis(
                kind=kind,
                requires_tools=True,
                categories=cats_sorted,
                detected_language=lang,
                fast_tool=tool_name,
                fast_args=tool_args,
                summary=f"Execute verified tool '{tool_name}'.",
            )

        # 5. Distinguish multi-step task vs system query vs direct action
        if _COMPOUND_MARKERS.search(low) or ("unnecessary" in low and "process" in low):
            return IntentAnalysis(
                kind=IntentKind.MULTI_STEP_TASK,
                requires_tools=True,
                categories=cats_sorted,
                detected_language=lang,
                summary="Plan, execute, and verify multi-step desktop task.",
            )

        if matched_cats == {"system"} and any(
            w in low for w in ("check", "what", "how", "show", "explain", "monitor", "read", "status", "performance", "specs", "disk", "running")
        ):
            return IntentAnalysis(
                kind=IntentKind.SYSTEM_QUERY,
                requires_tools=True,
                categories=cats_sorted,
                detected_language=lang,
                summary="Query and verify live system telemetry.",
            )

        return IntentAnalysis(
            kind=IntentKind.DIRECT_ACTION,
            requires_tools=True,
            categories=cats_sorted,
            detected_language=lang,
            summary="Select and execute verified desktop operator action.",
        )

    @staticmethod
    def _match_fast_path(raw: str, low: str) -> Optional[tuple[str, Dict[str, Any], IntentKind]]:
        stripped = low.rstrip(".!? ").strip()

        # Date & time
        if stripped in {
            "what time is it", "what's the time", "what is the time", "current time",
            "what is the date", "what's today's date", "what day is it", "date and time",
            "read the time", "நேரம் என்ன", "இன்று என்ன தேதி",
        }:
            return ("get_datetime", {}, IntentKind.SYSTEM_QUERY)

        # Open VS Code cleanly
        if stripped in {"open vs code", "open vscode", "open visual studio code", "launch vs code", "start vs code"}:
            return ("open_vscode", {}, IntentKind.DIRECT_ACTION)

        # Open simple named app ("open notepad", "open calculator", "open blender")
        m_app = re.match(
            r"^(?:please\s+)?(?:open|launch|start)\s+(notepad|calculator|calc|blender|chrome|google chrome|edge|microsoft edge|firefox|brave|file explorer|explorer|task manager|paint|vlc|obs|spotify|discord|windows terminal)$",
            stripped,
        )
        if m_app:
            return ("open_application", {"app_name": m_app.group(1)}, IntentKind.DIRECT_ACTION)

        # Laptop performance & diagnostics
        if stripped in {
            "check my laptop performance", "check laptop performance", "check system performance",
            "check my computer performance", "how is my laptop performing", "run system diagnostics",
            "system diagnostics", "check system health",
        }:
            return ("run_system_diagnostics", {}, IntentKind.SYSTEM_QUERY)

        # System specs
        if stripped in {
            "read system specifications", "check system specs", "what are my system specs",
            "show system specs", "my laptop specs",
        }:
            return ("get_system_specs", {}, IntentKind.SYSTEM_QUERY)

        # Disk space
        if stripped in {"check disk space", "check my disk space", "how much storage is left", "check storage"}:
            return ("check_disk_space", {}, IntentKind.SYSTEM_QUERY)

        # Explain running processes
        if stripped in {
            "explain what is currently running on my computer",
            "what is currently running on my computer",
            "what is running on my computer",
            "list running processes",
            "show running processes",
        }:
            return ("list_running_processes", {"limit": 15, "sort_by": "memory"}, IntentKind.SYSTEM_QUERY)

        # Screenshot
        if stripped in {"take a screenshot", "take screenshot", "capture screen", "capture a screenshot"}:
            return ("take_screenshot", {}, IntentKind.DIRECT_ACTION)

        # Set volume to N%
        m_vol = re.match(r"^(?:set|change|adjust)\s+(?:the\s+)?volume\s+to\s+(\d{1,3})\s*%?$", stripped)
        if m_vol:
            return ("set_volume", {"level": min(100, int(m_vol.group(1)))}, IntentKind.DIRECT_ACTION)

        # Open Windows settings
        m_set = re.match(r"^open\s+(?:windows\s+)?([a-z\- ]+?)\s+settings$", stripped)
        if m_set:
            return ("open_windows_settings", {"page": m_set.group(1).strip()}, IntentKind.DIRECT_ACTION)
        if stripped == "open windows settings":
            return ("open_windows_settings", {"page": "home"}, IntentKind.DIRECT_ACTION)

        # Start timer ("set a timer for 5 minutes")
        m_timer = re.match(
            r"^(?:set|start)\s+(?:a\s+)?timer\s+for\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)(?:\s+(?:for|called)\s+(.+))?$",
            stripped,
        )
        if m_timer:
            val = float(m_timer.group(1))
            unit = m_timer.group(2)
            secs = val * (3600 if unit.startswith("h") else (60 if unit.startswith("m") else 1))
            label = (m_timer.group(3) or "Timer").strip()
            return ("start_timer", {"duration_seconds": secs, "label": label}, IntentKind.DIRECT_ACTION)

        # Find Blender project
        if stripped in {"find my blender project", "find my blender projects", "locate my blender project"}:
            return ("find_projects", {"query": "", "project_type": "blender"}, IntentKind.DIRECT_ACTION)

        # Organize Downloads folder
        if stripped in {"organize my downloads folder", "organize downloads folder", "organize my downloads"}:
            return ("organize_directory", {"directory": "Downloads", "dry_run": False}, IntentKind.DIRECT_ACTION)

        return None
