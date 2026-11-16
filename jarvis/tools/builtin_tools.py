"""Complete suite of verified Windows 11 desktop operator tools (Section 5 A–F)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jarvis.automation.app_controller import AppController
from jarvis.automation.developer import DeveloperAutomation
from jarvis.browser.controller import BrowserController
from jarvis.filesystem.document_reader import DocumentReader
from jarvis.filesystem.operations import FileSystemManager
from jarvis.filesystem.organizer import DirectoryOrganizer, DuplicateFinder
from jarvis.memory.knowledge_store import KnowledgeStore
from jarvis.memory.utilities_store import UtilitiesStore
from jarvis.security.policy import RiskLevel
from jarvis.system.controls import SystemController
from jarvis.system.monitor import SystemMonitor
from jarvis.tools.base import ToolParam, ToolSpec
from jarvis.tools.registry import ToolRegistry


def register_builtin_tools(
    registry: ToolRegistry,
    *,
    apps: AppController,
    fs: FileSystemManager,
    reader: DocumentReader,
    organizer: DirectoryOrganizer,
    duplicates: DuplicateFinder,
    monitor: SystemMonitor,
    sys_ctrl: SystemController,
    browser: BrowserController,
    dev: DeveloperAutomation,
    utilities: UtilitiesStore,
    knowledge: KnowledgeStore,
    workspace_dir: Path,
) -> None:
    """Register all verified operator tools into the ToolRegistry."""

    def _resolve_default_path(raw: str) -> str:
        """Resolve bare relative filenames into Jarvis's safe workspace directory."""
        p = Path(raw).expanduser()
        if not p.is_absolute():
            # Map well-known folder shorthand names like 'Downloads', 'Documents', 'Desktop'
            shorthand = raw.strip().strip("/\\").lower()
            home = Path.home()
            special = {
                "downloads": home / "Downloads",
                "my downloads": home / "Downloads",
                "documents": home / "Documents",
                "my documents": home / "Documents",
                "desktop": home / "Desktop",
                "pictures": home / "Pictures",
                "projects": home / "Projects",
                "workspace": workspace_dir,
            }
            if shorthand in special:
                return str(special[shorthand].resolve())
            return str((workspace_dir / p).resolve())
        return str(p)

    # =====================================================================
    # A. APPLICATION MANAGEMENT
    # =====================================================================
    registry.register(ToolSpec(
        name="open_application",
        category="application",
        description="Launch a desktop application by name (e.g. 'VS Code', 'Blender', 'Notepad', 'Chrome').",
        parameters=[
            ToolParam("app_name", "string", "Application name"),
            ToolParam("target_path", "string", "Optional file or project path to open", required=False),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: apps.open_application(a["app_name"], target_path=a.get("target_path")),
    ))

    registry.register(ToolSpec(
        name="close_application",
        category="application",
        description="Gracefully close a running desktop application and verify exit.",
        parameters=[
            ToolParam("app_name", "string", "Application name"),
            ToolParam("force", "boolean", "Force kill if unresponsive", required=False, default=False),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: apps.close_application(a["app_name"], force=a.get("force", False)),
    ))

    registry.register(ToolSpec(
        name="switch_application",
        category="application",
        description="Bring a running Windows 11 application window to the foreground.",
        parameters=[ToolParam("app_name", "string", "Application or window name")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: apps.switch_to_application(a["app_name"]),
    ))

    registry.register(ToolSpec(
        name="is_application_running",
        category="application",
        description="Detect whether an application is currently running and show PIDs & memory.",
        parameters=[ToolParam("app_name", "string", "Application name")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: apps.is_running(a["app_name"]),
    ))

    registry.register(ToolSpec(
        name="list_installed_apps",
        category="application",
        description="List locally discovered desktop applications that Jarvis can launch.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {"success": True, "verified": True, "apps": apps.registry.list_apps()[:80]},
    ))

    registry.register(ToolSpec(
        name="open_app_project",
        category="application",
        description="Open a specific project file or directory inside a target application.",
        parameters=[
            ToolParam("app_name", "string", "Application name (e.g. 'code', 'blender')"),
            ToolParam("project_path", "string", "Full path to project file or directory"),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: apps.open_application(a["app_name"], target_path=a["project_path"]),
    ))

    # =====================================================================
    # B. FILE MANAGEMENT
    # =====================================================================
    registry.register(ToolSpec(
        name="search_files",
        category="filesystem",
        description="Search permitted directories/drives for files or folders by name and extension.",
        parameters=[
            ToolParam("query", "string", "Filename substring or glob pattern", required=False, default=""),
            ToolParam("extension", "string", "File extension filter (e.g. '.blend', '.pdf', '.py')", required=False, default=""),
            ToolParam("directory", "string", "Specific permitted folder or drive root", required=False),
            ToolParam("item_type", "string", "Filter type", required=False, default="all", enum=["all", "file", "folder"]),
        ],
        risk_level=RiskLevel.SAFE,
        accepts_token=True,
        handler=lambda a, t: fs.search_files(
            query=a.get("query", ""),
            extension=a.get("extension", ""),
            directory=_resolve_default_path(a["directory"]) if a.get("directory") else None,
            item_type=a.get("item_type", "all"),
            token=t,
        ),
    ))

    registry.register(ToolSpec(
        name="list_directory",
        category="filesystem",
        description="List files and subfolders inside a permitted directory.",
        parameters=[ToolParam("path", "string", "Directory path")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: fs.list_directory(_resolve_default_path(a["path"])),
    ))

    registry.register(ToolSpec(
        name="read_document",
        category="filesystem",
        description="Read a supported document (.txt, .md, .py, .json, .csv, .log, .docx, .pdf, .xlsx, .blend).",
        parameters=[ToolParam("path", "string", "File path to read")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: reader.read(_resolve_default_path(a["path"])),
    ))

    registry.register(ToolSpec(
        name="create_file",
        category="filesystem",
        description="Create a new file with text/code content (never overwrites existing files without confirmation).",
        parameters=[
            ToolParam("path", "string", "Target file path (relative paths go to Jarvis workspace)"),
            ToolParam("content", "string", "File content to write"),
            ToolParam("overwrite", "boolean", "Overwrite if exists (triggers confirmation)", required=False, default=False),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: fs.create_file(
            _resolve_default_path(a["path"]),
            a["content"],
            overwrite=a.get("overwrite", False),
        ),
    ))

    registry.register(ToolSpec(
        name="create_folder",
        category="filesystem",
        description="Create a new directory inside permitted paths.",
        parameters=[ToolParam("path", "string", "Folder path to create")],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: fs.create_folder(_resolve_default_path(a["path"])),
    ))

    registry.register(ToolSpec(
        name="rename_path",
        category="filesystem",
        description="Rename a file or folder safely without overwriting existing items.",
        parameters=[
            ToolParam("source", "string", "Existing file/folder path"),
            ToolParam("new_name", "string", "New file or folder name"),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: fs.rename_path(_resolve_default_path(a["source"]), a["new_name"]),
    ))

    registry.register(ToolSpec(
        name="move_path",
        category="filesystem",
        description="Move a file or folder to a destination directory safely.",
        parameters=[
            ToolParam("source", "string", "Source path"),
            ToolParam("destination", "string", "Destination path or folder"),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: fs.move_path(
            _resolve_default_path(a["source"]),
            _resolve_default_path(a["destination"]),
        ),
    ))

    registry.register(ToolSpec(
        name="copy_path",
        category="filesystem",
        description="Copy a file or folder and verify exact copied size.",
        parameters=[
            ToolParam("source", "string", "Source path"),
            ToolParam("destination", "string", "Destination path or folder"),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: fs.copy_path(
            _resolve_default_path(a["source"]),
            _resolve_default_path(a["destination"]),
        ),
    ))

    registry.register(ToolSpec(
        name="delete_path",
        category="filesystem",
        description="Send a file or folder to Recycle Bin (ALWAYS requires user confirmation).",
        parameters=[ToolParam("path", "string", "File or folder path to delete")],
        risk_level=RiskLevel.SENSITIVE,
        handler=lambda a, _t: fs.delete_path(_resolve_default_path(a["path"])),
    ))

    registry.register(ToolSpec(
        name="organize_directory",
        category="filesystem",
        description="Organize files in a folder (e.g. Downloads) into categorized subfolders with manifest & zero overwrites.",
        parameters=[
            ToolParam("directory", "string", "Directory to organize (e.g. Downloads path)"),
            ToolParam("dry_run", "boolean", "Preview changes without moving files", required=False, default=False),
        ],
        risk_level=RiskLevel.MODERATE,
        accepts_token=True,
        handler=lambda a, t: organizer.organize(
            _resolve_default_path(a["directory"]),
            dry_run=a.get("dry_run", False),
            token=t,
        ),
    ))

    registry.register(ToolSpec(
        name="find_duplicates",
        category="filesystem",
        description="Find exact SHA-256 duplicate files in a folder without deleting anything.",
        parameters=[
            ToolParam("directory", "string", "Directory to scan"),
            ToolParam("min_size_bytes", "integer", "Ignore files smaller than this", required=False, default=1),
        ],
        risk_level=RiskLevel.SAFE,
        accepts_token=True,
        handler=lambda a, t: duplicates.find_duplicates(
            _resolve_default_path(a["directory"]),
            min_size_bytes=a.get("min_size_bytes", 1),
            token=t,
        ),
    ))

    # =====================================================================
    # C. SYSTEM MANAGEMENT
    # =====================================================================
    registry.register(ToolSpec(
        name="get_system_specs",
        category="system",
        description="Read OS, CPU, RAM, NVIDIA GPU, storage drives, and uptime specifications.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {"success": True, "verified": True, **monitor.get_system_specs()},
    ))

    registry.register(ToolSpec(
        name="get_system_performance",
        category="system",
        description="Monitor live CPU %, RAM usage, GPU utilization & VRAM, battery, and disk usage.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {
            "success": True,
            "verified": True,
            **monitor.snapshot(sample_interval=0.15, include_disks=True).to_dict(),
        },
    ))

    registry.register(ToolSpec(
        name="run_system_diagnostics",
        category="system",
        description="Run full laptop performance & health diagnostics with bottleneck warnings and top processes.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {"success": True, "verified": True, **monitor.run_diagnostics()},
    ))

    registry.register(ToolSpec(
        name="check_disk_space",
        category="system",
        description="Check total, used, and free storage across all mounted drives.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {"success": True, "verified": True, "disks": monitor.get_disk_space()},
    ))

    registry.register(ToolSpec(
        name="list_running_processes",
        category="system",
        description="List currently running processes sorted by memory or CPU usage.",
        parameters=[
            ToolParam("limit", "integer", "Number of top processes", required=False, default=15),
            ToolParam("sort_by", "string", "Sort criterion", required=False, default="memory", enum=["memory", "cpu"]),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {
            "success": True,
            "verified": True,
            "processes": monitor.get_top_processes(limit=a.get("limit", 15), sort_by=a.get("sort_by", "memory")),
        },
    ))

    registry.register(ToolSpec(
        name="find_unnecessary_processes",
        category="system",
        description="Identify non-essential background processes consuming memory/CPU (protects all system processes).",
        parameters=[
            ToolParam("min_memory_mb", "number", "Minimum RAM MB threshold", required=False, default=50.0),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {
            "success": True,
            "verified": True,
            **sys_ctrl.find_unnecessary_processes(min_memory_mb=a.get("min_memory_mb", 50.0)),
        },
    ))

    registry.register(ToolSpec(
        name="stop_process",
        category="system",
        description="Gracefully terminate a permitted non-system process by name or PID (requires confirmation).",
        parameters=[ToolParam("name_or_pid", "string", "Process executable name or numeric PID")],
        risk_level=RiskLevel.SENSITIVE,
        handler=lambda a, _t: sys_ctrl.stop_process(a["name_or_pid"]),
    ))

    registry.register(ToolSpec(
        name="get_volume",
        category="system",
        description="Read current Windows 11 master speaker volume and mute status.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {"success": True, **sys_ctrl.get_volume()},
    ))

    registry.register(ToolSpec(
        name="set_volume",
        category="system",
        description="Set Windows 11 master volume (0-100), adjust by delta, or mute/unmute.",
        parameters=[
            ToolParam("level", "integer", "Target volume percent 0-100", required=False),
            ToolParam("delta", "integer", "Relative change e.g. +10 or -15", required=False),
            ToolParam("mute", "boolean", "True to mute, False to unmute", required=False),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: sys_ctrl.set_volume(
            level=a.get("level"), mute=a.get("mute"), delta=a.get("delta")
        ),
    ))

    registry.register(ToolSpec(
        name="take_screenshot",
        category="system",
        description="Capture a screenshot of the screen and save to screenshots folder.",
        parameters=[ToolParam("output_path", "string", "Optional custom output PNG path", required=False)],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: sys_ctrl.take_screenshot(
            _resolve_default_path(a["output_path"]) if a.get("output_path") else None
        ),
    ))

    registry.register(ToolSpec(
        name="open_windows_settings",
        category="system",
        description="Open a Windows 11 Settings page (e.g. 'bluetooth', 'wifi', 'display', 'sound', 'battery', 'storage', 'update').",
        parameters=[ToolParam("page", "string", "Settings section name", required=False, default="home")],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: sys_ctrl.open_windows_settings(a.get("page", "home")),
    ))

    # =====================================================================
    # D. BROWSER AUTOMATION
    # =====================================================================
    registry.register(ToolSpec(
        name="open_website",
        category="browser",
        description="Open a website URL in the default browser.",
        parameters=[ToolParam("url", "string", "Website URL (e.g. https://docs.python.org)")],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: browser.open_website(a["url"]),
    ))

    registry.register(ToolSpec(
        name="web_search",
        category="browser",
        description="Search the web in the browser when explicitly requested by the user.",
        parameters=[
            ToolParam("query", "string", "Search query"),
            ToolParam("engine", "string", "Search engine", required=False, default="duckduckgo",
                      enum=["duckduckgo", "google", "bing", "youtube", "github"]),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: browser.web_search(a["query"], engine=a.get("engine")),
    ))

    registry.register(ToolSpec(
        name="browser_navigate",
        category="browser",
        description="Navigate Playwright-controlled browser to a URL (optionally in a new tab).",
        parameters=[
            ToolParam("url", "string", "Target URL"),
            ToolParam("new_tab", "boolean", "Open in new tab", required=False, default=False),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: browser.navigate(a["url"], new_tab=a.get("new_tab", False)),
    ))

    registry.register(ToolSpec(
        name="browser_list_tabs",
        category="browser",
        description="List open tabs in the Playwright automated browser.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: browser.list_tabs(),
    ))

    registry.register(ToolSpec(
        name="browser_switch_tab",
        category="browser",
        description="Switch active tab by index in Playwright browser.",
        parameters=[ToolParam("tab_index", "integer", "0-based tab index")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: browser.switch_tab(a["tab_index"]),
    ))

    registry.register(ToolSpec(
        name="browser_close_tab",
        category="browser",
        description="Close a tab in Playwright browser.",
        parameters=[ToolParam("tab_index", "integer", "Optional 0-based tab index", required=False)],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: browser.close_tab(a.get("tab_index")),
    ))

    registry.register(ToolSpec(
        name="browser_click",
        category="browser",
        description="Click an element by CSS/text selector in the active Playwright page.",
        parameters=[ToolParam("selector", "string", "Playwright CSS or text= selector")],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: browser.click(a["selector"]),
    ))

    registry.register(ToolSpec(
        name="browser_fill",
        category="browser",
        description="Fill an input field in the active Playwright page and verify value.",
        parameters=[
            ToolParam("selector", "string", "Input selector"),
            ToolParam("value", "string", "Text to enter"),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: browser.fill(a["selector"], a["value"]),
    ))

    registry.register(ToolSpec(
        name="browser_read_page",
        category="browser",
        description="Extract visible text from the active Playwright browser page.",
        parameters=[ToolParam("max_chars", "integer", "Character limit", required=False, default=4000)],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: browser.read_page(max_chars=a.get("max_chars", 4000)),
    ))

    # =====================================================================
    # E. DEVELOPER & BLENDER WORKFLOW
    # =====================================================================
    registry.register(ToolSpec(
        name="open_vscode",
        category="developer",
        description="Open Visual Studio Code, optionally targeting a specific project folder or file.",
        parameters=[ToolParam("project_path", "string", "Optional folder or file path", required=False)],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: dev.open_vscode(
            _resolve_default_path(a["project_path"]) if a.get("project_path") else None
        ),
    ))

    registry.register(ToolSpec(
        name="find_projects",
        category="developer",
        description="Find coding repositories (Python, Node, Git) or Blender (.blend) projects in permitted directories.",
        parameters=[
            ToolParam("query", "string", "Project name or keyword", required=False, default=""),
            ToolParam("project_type", "string", "Type filter", required=False, default="all",
                      enum=["all", "blender", "python", "node", "git"]),
        ],
        risk_level=RiskLevel.SAFE,
        accepts_token=True,
        handler=lambda a, t: dev.find_projects(
            query=a.get("query", ""),
            project_type=a.get("project_type", "all"),
            token=t,
        ),
    ))

    registry.register(ToolSpec(
        name="inspect_blend_project",
        category="developer",
        description="Inspect a Blender (.blend) project's header validity, version, backups, assets & crash logs.",
        parameters=[ToolParam("blend_path", "string", "Full path to .blend file")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: reader.inspect_blend_file(_resolve_default_path(a["blend_path"])),
    ))

    registry.register(ToolSpec(
        name="open_blender_project",
        category="developer",
        description="Verify a .blend project's integrity, check GPU VRAM, and open it in Blender.",
        parameters=[
            ToolParam("blend_path", "string", "Path to .blend file"),
            ToolParam("launch_blender", "boolean", "Launch Blender after checking file", required=False, default=True),
        ],
        risk_level=RiskLevel.MODERATE,
        handler=lambda a, _t: dev.open_and_check_blender_project(
            _resolve_default_path(a["blend_path"]),
            launch_blender=a.get("launch_blender", True),
        ),
    ))

    registry.register(ToolSpec(
        name="run_terminal_command",
        category="developer",
        description="Run a single validated terminal command inside a permitted working directory.",
        parameters=[
            ToolParam("command", "string", "Command to execute (no chaining or destructive flags)"),
            ToolParam("cwd", "string", "Optional permitted working directory", required=False),
            ToolParam("timeout_sec", "number", "Max runtime seconds", required=False, default=30.0),
        ],
        risk_level=RiskLevel.MODERATE,
        accepts_token=True,
        handler=lambda a, t: dev.run_terminal_command(
            a["command"],
            cwd=_resolve_default_path(a["cwd"]) if a.get("cwd") else None,
            timeout_sec=a.get("timeout_sec", 30.0),
            token=t,
        ),
    ))

    registry.register(ToolSpec(
        name="run_python_script",
        category="developer",
        description="Execute a local .py script, capture stdout/stderr, verify exit code & summarize any traceback.",
        parameters=[
            ToolParam("script_path", "string", "Path to .py file"),
            ToolParam("args", "array", "Optional CLI argument strings", required=False),
            ToolParam("timeout_sec", "number", "Timeout seconds", required=False, default=30.0),
        ],
        risk_level=RiskLevel.MODERATE,
        accepts_token=True,
        handler=lambda a, t: dev.run_python_script(
            _resolve_default_path(a["script_path"]),
            args=a.get("args"),
            timeout_sec=a.get("timeout_sec", 30.0),
            token=t,
        ),
    ))

    registry.register(ToolSpec(
        name="list_dev_processes",
        category="developer",
        description="Monitor running developer processes (Python, Node, VS Code, Blender, Git, Docker, Ollama).",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: dev.list_dev_processes(),
    ))

    registry.register(ToolSpec(
        name="analyze_error_log",
        category="developer",
        description="Read an application/build error log and extract recent exceptions and file:line references.",
        parameters=[ToolParam("log_path", "string", "Path to .log or text file")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: dev.analyze_error_log(_resolve_default_path(a["log_path"])),
    ))

    # =====================================================================
    # F. PERSONAL UTILITIES & PERSISTENT MEMORY
    # =====================================================================
    registry.register(ToolSpec(
        name="get_datetime",
        category="utilities",
        description="Read the current local date, time, day of week, and timezone.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: _get_datetime_payload(),
    ))

    registry.register(ToolSpec(
        name="create_reminder",
        category="utilities",
        description="Create a local reminder (e.g. when='in 20 minutes' or '18:30').",
        parameters=[
            ToolParam("message", "string", "Reminder text"),
            ToolParam("when", "string", "Relative or clock time (e.g. 'in 15 minutes', '19:00')"),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "reminder": utilities.create_reminder(a["message"], a["when"])},
    ))

    registry.register(ToolSpec(
        name="list_reminders",
        category="utilities",
        description="List upcoming active reminders.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {"success": True, "verified": True, "reminders": utilities.list_reminders()},
    ))

    registry.register(ToolSpec(
        name="save_note",
        category="utilities",
        description="Save a persistent personal note with title, content, and optional tags.",
        parameters=[
            ToolParam("title", "string", "Note title"),
            ToolParam("content", "string", "Note body text"),
            ToolParam("tags", "string", "Comma-separated tags", required=False, default=""),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "note": utilities.save_note(a["title"], a["content"], a.get("tags", ""))},
    ))

    registry.register(ToolSpec(
        name="search_notes",
        category="utilities",
        description="Search or list saved personal notes.",
        parameters=[ToolParam("query", "string", "Search keyword (empty lists recent notes)", required=False, default="")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "notes": utilities.search_notes(a.get("query", ""))},
    ))

    registry.register(ToolSpec(
        name="add_todo",
        category="utilities",
        description="Add an item to the personal to-do list.",
        parameters=[
            ToolParam("title", "string", "Task description"),
            ToolParam("priority", "string", "Priority level", required=False, default="medium", enum=["low", "medium", "high"]),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "todo": utilities.add_todo(a["title"], a.get("priority", "medium"))},
    ))

    registry.register(ToolSpec(
        name="list_todos",
        category="utilities",
        description="List personal to-do tasks.",
        parameters=[
            ToolParam("status", "string", "Filter by status", required=False, default="pending", enum=["all", "pending", "completed"]),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "todos": utilities.list_todos(a.get("status", "pending"))},
    ))

    registry.register(ToolSpec(
        name="update_todo",
        category="utilities",
        description="Mark a to-do item as completed, pending, or cancelled by its ID.",
        parameters=[
            ToolParam("todo_id", "integer", "Numeric to-do ID"),
            ToolParam("status", "string", "New status", required=False, default="completed", enum=["pending", "completed", "cancelled"]),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: _update_todo_payload(utilities, a["todo_id"], a.get("status", "completed")),
    ))

    registry.register(ToolSpec(
        name="start_timer",
        category="utilities",
        description="Start a countdown timer in seconds with an optional label.",
        parameters=[
            ToolParam("duration_seconds", "number", "Countdown length in seconds"),
            ToolParam("label", "string", "Timer label", required=False, default="Timer"),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "timer": utilities.start_timer(a["duration_seconds"], a.get("label", "Timer"))},
    ))

    registry.register(ToolSpec(
        name="list_timers",
        category="utilities",
        description="List currently running countdown timers and remaining time.",
        parameters=[],
        risk_level=RiskLevel.SAFE,
        handler=lambda _a, _t: {"success": True, "verified": True, "timers": utilities.list_timers()},
    ))

    registry.register(ToolSpec(
        name="cancel_timer",
        category="utilities",
        description="Cancel a running timer by its ID or label.",
        parameters=[ToolParam("timer_id_or_label", "string", "Timer ID or label")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {
            "success": (ok := utilities.cancel_timer(a["timer_id_or_label"])),
            "verified": True,
            "error": None if ok else f"No running timer matching '{a['timer_id_or_label']}'.",
        },
    ))

    registry.register(ToolSpec(
        name="get_task_history",
        category="utilities",
        description="Read recent history of tasks executed by Jarvis and their verification outcomes.",
        parameters=[ToolParam("limit", "integer", "Max records", required=False, default=15)],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "history": utilities.get_task_history(a.get("limit", 15))},
    ))

    registry.register(ToolSpec(
        name="remember_fact",
        category="memory",
        description="Persist an important user preference, fact, or project path in long-term local memory.",
        parameters=[
            ToolParam("key", "string", "Short descriptive key (e.g. 'blender_edu_project')"),
            ToolParam("value", "string", "Fact or path to remember"),
            ToolParam("category", "string", "Category tag", required=False, default="general"),
            ToolParam("pinned", "boolean", "Always include in agent context", required=False, default=False),
        ],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {
            "success": True,
            "verified": True,
            "fact": knowledge.remember(a["key"], a["value"], a.get("category", "general"), a.get("pinned", False)),
        },
    ))

    registry.register(ToolSpec(
        name="recall_facts",
        category="memory",
        description="Search persistent local memory for stored user preferences, notes, and project locations.",
        parameters=[ToolParam("query", "string", "Search query", required=False, default="")],
        risk_level=RiskLevel.SAFE,
        handler=lambda a, _t: {"success": True, "verified": True, "facts": knowledge.recall(a.get("query", ""))},
    ))


def _get_datetime_payload() -> dict:
    now = datetime.now().astimezone()
    return {
        "success": True,
        "verified": True,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "time_12h": now.strftime("%I:%M %p"),
        "day_of_week": now.strftime("%A"),
        "timezone": str(now.tzinfo),
        "human": now.strftime("%A, %B %d, %Y at %I:%M %p"),
    }


def _update_todo_payload(utilities: UtilitiesStore, todo_id: int, status: str) -> dict:
    updated = utilities.update_todo(todo_id, status)
    if updated is None:
        return {"success": False, "verified": True, "error": f"To-do #{todo_id} not found."}
    return {"success": True, "verified": True, "todo": updated}
