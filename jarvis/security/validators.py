"""Defense-in-depth validators: PathGuard, CommandValidator, AppAllowlist, WebGuard.

Never trusts LLM-generated paths, shell commands, or app names directly.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import List, Optional, Tuple

from jarvis.config.schema import SecurityConfig
from jarvis.security.policy import PermissionCheckResult, PermissionDecision, RiskLevel


# Critical system/credential path segments that Jarvis must NEVER modify or read blindly
_PROTECTED_PATH_PARTS = {
    "windows",
    "system32",
    "syswow64",
    "winsxs",
    "boot",
    "efi",
    "recovery",
    "$recycle.bin",
    "system volume information",
    "windows defender",
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
}

_PROTECTED_UNIX_PREFIXES = (
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
    "/root",
    "/var/lib",
)

_IMPORTANT_FILE_EXTENSIONS = {
    ".env",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".kdbx",
    ".blend",
    ".docx",
    ".xlsx",
    ".pptx",
    ".pdf",
    ".sqlite",
    ".db",
}


class PathGuard:
    """Enforces permitted directory & drive boundaries and protects sensitive files."""

    def __init__(self, security_cfg: SecurityConfig) -> None:
        self.cfg = security_cfg

    def permitted_roots(self) -> List[Path]:
        roots: List[Path] = []
        for raw in self.cfg.permitted_directories:
            try:
                roots.append(Path(os.path.expandvars(raw)).expanduser().resolve())
            except Exception:
                continue
        return roots

    def is_within_permitted_dirs(self, target: Path) -> bool:
        resolved = target.resolve()
        for root in self.permitted_roots():
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def is_within_permitted_drives(self, target: Path) -> bool:
        drive = target.resolve().drive.upper()
        if not drive:
            # Non-Windows POSIX environment: rely strictly on permitted_directories
            return self.is_within_permitted_dirs(target)
        allowed = {d.strip().upper().rstrip("\\/") for d in self.cfg.permitted_drives}
        return drive in allowed

    def _hits_protected_location(self, target: Path) -> Optional[str]:
        resolved = target.resolve()
        posix = resolved.as_posix()
        if os.name != "nt":
            for prefix in _PROTECTED_UNIX_PREFIXES:
                if posix == prefix or posix.startswith(prefix + "/"):
                    return f"Protected system path '{prefix}'"

        parts_lower = [p.lower() for p in resolved.parts]
        # Block C:\Windows\... explicitly on Windows
        if len(parts_lower) >= 2 and parts_lower[1] in {"windows", "boot", "recovery", "$recycle.bin", "system volume information"}:
            return f"Protected Windows system directory '{resolved.parts[1]}'"
        for sensitive in (".ssh", ".gnupg", ".aws", ".azure", ".kube", "windows defender", "system32", "syswow64"):
            if sensitive in parts_lower:
                return f"Protected security/credential directory '{sensitive}'"
        if resolved.suffix.lower() in {".pem", ".key", ".pfx", ".p12", ".kdbx"} or resolved.name.lower() in {".env", ".netrc", "id_rsa", "id_ed25519", "sam", "system", "security"}:
            return f"Sensitive credential/security file '{resolved.name}'"
        return None

    def check_read(self, raw_path: str, allow_drive_search: bool = False) -> Tuple[Path, PermissionCheckResult]:
        target = Path(os.path.expandvars(raw_path)).expanduser().resolve()
        protected = self._hits_protected_location(target)
        if protected:
            return target, PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason=f"Access denied: {protected}.",
                target_summary=str(target),
            )
        in_dir = self.is_within_permitted_dirs(target)
        in_drive = allow_drive_search and self.is_within_permitted_drives(target)
        if not (in_dir or in_drive):
            return target, PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason=(
                    f"Path '{target}' is outside permitted directories. "
                    "Add it in Permissions settings to grant access."
                ),
                target_summary=str(target),
            )
        return target, PermissionCheckResult(
            decision=PermissionDecision.ALLOW,
            risk_level=RiskLevel.SAFE,
            reason="Read permitted inside configured boundary.",
            target_summary=str(target),
        )

    def check_write(
        self,
        raw_path: str,
        allow_overwrite: bool = False,
        is_delete: bool = False,
    ) -> Tuple[Path, PermissionCheckResult]:
        target = Path(os.path.expandvars(raw_path)).expanduser().resolve()
        protected = self._hits_protected_location(target)
        if protected:
            return target, PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason=f"Write/delete denied: {protected}.",
                target_summary=str(target),
            )
        if not self.is_within_permitted_dirs(target):
            return target, PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason=f"Mutation blocked: '{target}' is outside permitted directories.",
                target_summary=str(target),
            )
        # Never permit deleting a root permitted directory itself (e.g. ~/Documents)
        if is_delete and target in self.permitted_roots():
            return target, PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason=f"Refusing to delete top-level user directory '{target}'.",
                target_summary=str(target),
            )
        if is_delete:
            decision = (
                PermissionDecision.REQUIRE_CONFIRMATION
                if self.cfg.require_confirmation_for_delete
                else PermissionDecision.ALLOW
            )
            return target, PermissionCheckResult(
                decision=decision,
                risk_level=RiskLevel.SENSITIVE,
                reason=f"Deleting '{target.name}' requires explicit user confirmation.",
                target_summary=str(target),
            )
        if target.exists():
            if not allow_overwrite:
                return target, PermissionCheckResult(
                    decision=PermissionDecision.REQUIRE_CONFIRMATION
                    if self.cfg.require_confirmation_for_overwrite
                    else PermissionDecision.ALLOW,
                    risk_level=RiskLevel.SENSITIVE,
                    reason=f"File '{target.name}' already exists; overwriting requires confirmation.",
                    target_summary=str(target),
                )
            if target.suffix.lower() in _IMPORTANT_FILE_EXTENSIONS and self.cfg.require_confirmation_for_overwrite:
                return target, PermissionCheckResult(
                    decision=PermissionDecision.REQUIRE_CONFIRMATION,
                    risk_level=RiskLevel.SENSITIVE,
                    reason=f"Overwriting important document '{target.name}' requires confirmation.",
                    target_summary=str(target),
                )
        return target, PermissionCheckResult(
            decision=PermissionDecision.ALLOW,
            risk_level=RiskLevel.MODERATE,
            reason="Write allowed inside permitted directory.",
            target_summary=str(target),
        )


# Hard-blocked destructive / obfuscated / security-tampering command patterns
_BLOCKED_COMMAND_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\b(rm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r|rm\s+--no-preserve-root)\b"), "Recursive forced delete (rm -rf)"),
    (re.compile(r"(?i)\b(del|erase)\s+.*(/[sqf])"), "Destructive Windows del command"),
    (re.compile(r"(?i)\b(rd|rmdir)\s+.*(/[sq])"), "Recursive Windows directory removal"),
    (re.compile(r"(?i)\bRemove-Item\b"), "PowerShell Remove-Item (use validated delete_path tool instead)"),
    (re.compile(r"(?i)\b(format(\.com)?|diskpart|fdisk|mkfs|dd\s+if=|cipher\s+/w)\b"), "Disk formatting / raw wipe command"),
    (re.compile(r"(?i)\b(bcdedit|bootrec|reagentc|sfc\s+/scannow|dism\s+/online)\b"), "Boot/OS integrity modification"),
    (re.compile(r"(?i)\b(reg\s+(delete|add|import)|Remove-ItemProperty|Set-ItemProperty\s+HKLM)\b"), "Windows Registry modification"),
    (re.compile(r"(?i)\b(Set-MpPreference|Add-MpPreference|netsh\s+advfirewall|ufw\s+disable|Disable-NetFirewallRule)\b"), "Security/Firewall/Defender tampering"),
    (re.compile(r"(?i)\b(sc\s+(delete|stop|config)|Stop-Service|Set-Service)\b"), "System service tampering"),
    (re.compile(r"(?i)\b(takeown|icacls|chmod\s+777|chown\s+-R|net\s+user|net\s+localgroup)\b"), "Account or ACL privilege modification"),
    (re.compile(r"(?i)\b(shutdown|restart-computer|stop-computer|reboot|poweroff|halt)\b"), "System shutdown/reboot via raw shell"),
    (re.compile(r"(?i)(-enc|-encodedcommand|frombase64string|invoke-expression|\biex\b|downloadstring|downloadfile)"), "Obfuscated or remote code execution"),
    (re.compile(r"(?i)(curl|wget|invoke-webrequest|iwr)\b.*\|\s*(sh|bash|pwsh|powershell|cmd|python)"), "Pipe-to-shell remote execution"),
    (re.compile(r"(?i)\b(taskkill\s+/f|Stop-Process\s+-Force|kill\s+-9|killall|pkill)\b"), "Unvalidated forced process kill (use stop_process tool)"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "Fork bomb"),
]

# Shell injection metacharacters (prevents `echo hi && del /q ...`)
_SHELL_CHAIN_PATTERN = re.compile(r"(;|&&|\|\||`|\$\(|\r|\n|>\s*/|>\s*[A-Za-z]:\\Windows)", re.IGNORECASE)

# Commands allowed ONLY after explicit human confirmation
_CONFIRM_COMMAND_PATTERNS: List[Tuple[re.Pattern, RiskLevel, str]] = [
    (re.compile(r"(?i)\b(winget\s+install|choco\s+install|scoop\s+install|pip3?\s+install|npm\s+(install|i)\b|cargo\s+install|apt(-get)?\s+install)\b"), RiskLevel.SENSITIVE, "Software/package installation requires confirmation."),
    (re.compile(r"(?i)\b(winget\s+uninstall|pip3?\s+uninstall|npm\s+uninstall|choco\s+uninstall)\b"), RiskLevel.SENSITIVE, "Software uninstallation requires confirmation."),
    (re.compile(r"(?i)\b(runas|sudo|gsudo|Start-Process\b.*-Verb\s+RunAs)\b"), RiskLevel.CRITICAL, "Administrator privilege escalation requires confirmation."),
    (re.compile(r"(?i)\b(git\s+(push|reset\s+--hard|clean\s+-f|checkout\s+--))\b"), RiskLevel.SENSITIVE, "Potentially destructive or remote git command requires confirmation."),
    (re.compile(r"(?i)\b(Send-MailMessage|sendmail|mutt)\b"), RiskLevel.SENSITIVE, "Sending email/messages requires confirmation."),
    (re.compile(r"(?i)\b(rm|del|erase|rmdir|rd)\b"), RiskLevel.SENSITIVE, "File deletion command requires confirmation."),
]


class CommandValidator:
    """Validates terminal commands before subprocess execution."""

    def __init__(self, security_cfg: SecurityConfig) -> None:
        self.cfg = security_cfg

    def validate(self, command: str) -> PermissionCheckResult:
        cmd = (command or "").strip()
        if not cmd:
            return PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason="Empty command rejected.",
                target_summary="",
            )
        if len(cmd) > 1000:
            return PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason="Command exceeds 1000-character safety limit.",
                target_summary=cmd[:120],
            )

        for pattern, desc in _BLOCKED_COMMAND_PATTERNS:
            if pattern.search(cmd):
                return PermissionCheckResult(
                    decision=PermissionDecision.DENY,
                    risk_level=RiskLevel.BLOCKED,
                    reason=f"Blocked unsafe command: {desc}.",
                    target_summary=cmd,
                )

        if _SHELL_CHAIN_PATTERN.search(cmd):
            return PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason="Blocked shell command chaining/subshell operators (; && || ` $()). Run one clean command at a time.",
                target_summary=cmd,
            )

        for pattern, risk, reason in _CONFIRM_COMMAND_PATTERNS:
            if pattern.search(cmd):
                return PermissionCheckResult(
                    decision=PermissionDecision.REQUIRE_CONFIRMATION,
                    risk_level=risk,
                    reason=reason,
                    target_summary=cmd,
                )

        # Block inline destructive Python one-liners via `python -c "os.remove..."`
        if re.search(r"(?i)\b(python3?|py)\s+-c\b.*\b(rmtree|unlink|remove|system|Popen|subprocess|socket|urllib|requests|httpx)\b", cmd):
            return PermissionCheckResult(
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                risk_level=RiskLevel.SENSITIVE,
                reason="Inline Python command accesses filesystem/process/network APIs; requires confirmation.",
                target_summary=cmd,
            )

        # Check approved safe prefixes
        cmd_lower = cmd.lower()
        for prefix in self.cfg.approved_terminal_prefixes:
            p = prefix.strip().lower()
            if cmd_lower == p or cmd_lower.startswith(p + " "):
                return PermissionCheckResult(
                    decision=PermissionDecision.ALLOW,
                    risk_level=RiskLevel.MODERATE,
                    reason=f"Matches approved command prefix '{prefix}'.",
                    target_summary=cmd,
                )

        return PermissionCheckResult(
            decision=PermissionDecision.REQUIRE_CONFIRMATION,
            risk_level=RiskLevel.SENSITIVE,
            reason=f"Command '{self._first_token(cmd)}' is not in the pre-approved command list; confirmation required.",
            target_summary=cmd,
        )

    @staticmethod
    def _first_token(cmd: str) -> str:
        try:
            tokens = shlex.split(cmd, posix=(os.name != "nt"))
            return tokens[0] if tokens else cmd[:32]
        except ValueError:
            return cmd.split()[0][:32]


# Canonical alias mapping so natural names check cleanly against the allowlist
APP_CANONICAL_ALIASES = {
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "blender": "blender",
    "blender 3d": "blender",
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "file explorer": "explorer",
    "windows explorer": "explorer",
    "explorer": "explorer",
    "paint": "mspaint",
    "google chrome": "chrome",
    "chrome": "chrome",
    "microsoft edge": "msedge",
    "edge": "msedge",
    "firefox": "firefox",
    "mozilla firefox": "firefox",
    "brave": "brave",
    "brave browser": "brave",
    "windows terminal": "wt",
    "terminal": "wt",
    "powershell": "powershell",
    "command prompt": "cmd",
    "cmd": "cmd",
    "task manager": "taskmgr",
    "snipping tool": "snippingtool",
    "vlc": "vlc",
    "vlc media player": "vlc",
    "obs": "obs",
    "obs studio": "obs",
    "spotify": "spotify",
    "discord": "discord",
    "microsoft word": "word",
    "word": "word",
    "microsoft excel": "excel",
    "excel": "excel",
    "powerpoint": "powerpoint",
    "microsoft powerpoint": "powerpoint",
}

_BLOCKED_APPS = {
    "regedit",
    "registry editor",
    "diskpart",
    "format",
    "gpedit",
    "secpol",
    "powershell_ise_admin",
}


class AppAllowlistValidator:
    """Checks application launches against configured allowlist."""

    def __init__(self, security_cfg: SecurityConfig) -> None:
        self.cfg = security_cfg

    def normalize(self, app_name: str) -> str:
        cleaned = app_name.strip().lower()
        if cleaned.endswith(".exe"):
            cleaned = cleaned[:-4]
        return APP_CANONICAL_ALIASES.get(cleaned, cleaned)

    def check(self, app_name: str) -> PermissionCheckResult:
        raw = (app_name or "").strip()
        if not raw:
            return PermissionCheckResult(
                decision=PermissionDecision.DENY,
                risk_level=RiskLevel.BLOCKED,
                reason="Empty application name.",
            )
        canonical = self.normalize(raw)
        if canonical in _BLOCKED_APPS or raw.lower() in _BLOCKED_APPS:
            return PermissionCheckResult(
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                risk_level=RiskLevel.CRITICAL,
                reason=f"System security/registry utility '{raw}' requires explicit user confirmation.",
                target_summary=raw,
            )
        allowed = {self.normalize(a) for a in self.cfg.allowlisted_apps}
        if canonical in allowed or raw.lower() in allowed:
            return PermissionCheckResult(
                decision=PermissionDecision.ALLOW,
                risk_level=RiskLevel.MODERATE,
                reason=f"Application '{raw}' ({canonical}) is on the allowlist.",
                target_summary=raw,
            )
        decision = (
            PermissionDecision.REQUIRE_CONFIRMATION
            if self.cfg.require_confirmation_for_unlisted_app
            else PermissionDecision.ALLOW
        )
        return PermissionCheckResult(
            decision=decision,
            risk_level=RiskLevel.SENSITIVE,
            reason=f"Application '{raw}' is not in your allowlist; confirmation required.",
            target_summary=raw,
        )


_SENSITIVE_WEB_KEYWORDS = re.compile(
    r"(?i)\b(bank|netbanking|paypal|stripe|razorpay|upi|checkout|payment|pay\s*now|"
    r"credit.?card|cvv|transfer|wire|buy\s*now|place.?order|password|passwd|"
    r"send\s*mail|compose|submit\s*order|delete\s*account|gmail\.com.*compose|whatsapp|telegram)\b"
)


class WebSafetyValidator:
    """Validates URLs and flags financial, credential, or messaging browser actions."""

    def __init__(self, security_cfg: SecurityConfig) -> None:
        self.cfg = security_cfg

    def check_url(self, url: str) -> PermissionCheckResult:
        u = (url or "").strip()
        if not u:
            return PermissionCheckResult(PermissionDecision.DENY, RiskLevel.BLOCKED, "Empty URL.")
        if not u.startswith(("http://", "https://")):
            if re.match(r"^[a-zA-Z]+:", u):
                return PermissionCheckResult(
                    PermissionDecision.DENY,
                    RiskLevel.BLOCKED,
                    f"Blocked non-HTTP URL scheme in '{u[:60]}'.",
                    target_summary=u,
                )
            u = "https://" + u

        if self.cfg.require_confirmation_for_sensitive_web and _SENSITIVE_WEB_KEYWORDS.search(u):
            return PermissionCheckResult(
                PermissionDecision.REQUIRE_CONFIRMATION,
                RiskLevel.CRITICAL,
                "URL appears related to banking, payments, credentials, or messaging; confirmation required.",
                target_summary=u,
            )
        return PermissionCheckResult(
            PermissionDecision.ALLOW,
            RiskLevel.MODERATE,
            "Safe HTTP(S) URL.",
            target_summary=u,
        )

    def check_interaction(self, selector: str, value: str = "") -> PermissionCheckResult:
        combined = f"{selector} {value}"
        if self.cfg.require_confirmation_for_sensitive_web and _SENSITIVE_WEB_KEYWORDS.search(combined):
            return PermissionCheckResult(
                PermissionDecision.REQUIRE_CONFIRMATION,
                RiskLevel.CRITICAL,
                "Browser action involves sensitive payment, password, or message-sending element; confirmation required.",
                target_summary=selector[:120],
            )
        return PermissionCheckResult(
            PermissionDecision.ALLOW,
            RiskLevel.MODERATE,
            "Standard browser interaction allowed.",
            target_summary=selector[:120],
        )
