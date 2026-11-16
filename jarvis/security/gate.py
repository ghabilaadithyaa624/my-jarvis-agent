"""Central SecurityGate — single mandatory chokepoint for every tool invocation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from jarvis.config.schema import SecurityConfig
from jarvis.logs.logger import AuditLogger, get_logger
from jarvis.security.emergency import CancellationToken, EmergencyStopController
from jarvis.security.policy import (
    ConfirmationRequest,
    PermissionCheckResult,
    PermissionDecision,
    RiskLevel,
)
from jarvis.security.validators import (
    AppAllowlistValidator,
    CommandValidator,
    PathGuard,
    WebSafetyValidator,
)

log = get_logger("security.gate")

ConfirmationHandler = Callable[[ConfirmationRequest], bool]


class PermissionDeniedError(PermissionError):
    """Raised when SecurityGate denies or the user rejects an action."""


class SecurityGate:
    """Enforces allowlists, directory jails, command rules, confirmations, and audit logs."""

    def __init__(
        self,
        security_cfg: SecurityConfig,
        audit_logger: AuditLogger,
        emergency: EmergencyStopController,
        confirmation_handler: Optional[ConfirmationHandler] = None,
        on_audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        workspace_dir: Optional[Path] = None,
    ) -> None:
        self.cfg = security_cfg
        self.audit = audit_logger
        self.emergency = emergency
        self.workspace_dir = workspace_dir
        self.path_guard = PathGuard(security_cfg)
        self.cmd_validator = CommandValidator(security_cfg)
        self.app_validator = AppAllowlistValidator(security_cfg)
        self.web_validator = WebSafetyValidator(security_cfg)
        self._confirmation_handler: Optional[ConfirmationHandler] = confirmation_handler
        self._on_audit = on_audit_callback

    def resolve_tool_path(self, raw: str) -> str:
        """Resolve relative paths consistently with ToolRegistry (into workspace_dir)."""
        if not raw:
            return raw
        p = Path(raw).expanduser()
        if p.is_absolute():
            return str(p)
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
        }
        if self.workspace_dir:
            special["workspace"] = self.workspace_dir
        if shorthand in special:
            return str(special[shorthand].resolve())
        if self.workspace_dir:
            return str((self.workspace_dir / p).resolve())
        return str(p.resolve())

    def set_confirmation_handler(self, handler: Optional[ConfirmationHandler]) -> None:
        self._confirmation_handler = handler

    def _emit_audit(self, entry: Dict[str, Any]) -> None:
        if self._on_audit:
            try:
                self._on_audit(entry)
            except Exception:
                pass

    def authorize(
        self,
        tool_name: str,
        default_risk: RiskLevel,
        arguments: Dict[str, Any],
        specific_check: Optional[PermissionCheckResult] = None,
        token: Optional[CancellationToken] = None,
    ) -> PermissionCheckResult:
        """Evaluate permissions, prompt user if required, and log decision.

        Fail-closed invariant: if confirmation is required and no confirmation
        handler is registered (or it times out / throws), the action is DENIED.
        """
        self.emergency.check(token)

        check = specific_check or self._evaluate_tool(tool_name, default_risk, arguments)

        if check.decision == PermissionDecision.DENY:
            entry = self.audit.record(
                event_type="PERMISSION_CHECK",
                action=tool_name,
                decision="DENIED",
                risk_level=check.risk_level.value,
                details={"reason": check.reason, "target": check.target_summary, "args": arguments},
            )
            self._emit_audit(entry)
            log.warning("DENIED %s: %s", tool_name, check.reason)
            raise PermissionDeniedError(check.reason)

        if check.decision == PermissionDecision.REQUIRE_CONFIRMATION:
            if self._confirmation_handler is None:
                reason = f"Confirmation required for '{tool_name}' ({check.reason}), but no interactive confirmation channel is active — denied safely."
                entry = self.audit.record(
                    event_type="CONFIRMATION",
                    action=tool_name,
                    decision="DENIED_NO_HANDLER",
                    risk_level=check.risk_level.value,
                    details={"reason": check.reason, "target": check.target_summary, "args": arguments},
                )
                self._emit_audit(entry)
                raise PermissionDeniedError(reason)

            req = ConfirmationRequest(
                tool_name=tool_name,
                risk_level=check.risk_level,
                reason=check.reason,
                target_summary=check.target_summary or str(arguments)[:200],
                arguments=arguments,
                timeout_sec=self.cfg.confirmation_timeout_sec,
            )
            approved = False
            try:
                approved = bool(self._confirmation_handler(req))
            except Exception as exc:
                log.error("Confirmation handler error for %s: %s", tool_name, exc)
                approved = False

            self.emergency.check(token)

            decision_label = "USER_APPROVED" if approved else "USER_REJECTED"
            entry = self.audit.record(
                event_type="CONFIRMATION",
                action=tool_name,
                decision=decision_label,
                risk_level=check.risk_level.value,
                details={"reason": check.reason, "target": check.target_summary, "args": arguments},
            )
            self._emit_audit(entry)
            if not approved:
                raise PermissionDeniedError(f"User declined confirmation for '{tool_name}': {check.reason}")
            return check

        entry = self.audit.record(
            event_type="PERMISSION_CHECK",
            action=tool_name,
            decision="ALLOWED",
            risk_level=check.risk_level.value,
            details={"target": check.target_summary or tool_name},
        )
        self._emit_audit(entry)
        return check

    def record_outcome(
        self,
        tool_name: str,
        risk_level: RiskLevel,
        success: bool,
        verified: bool,
        summary: str,
    ) -> None:
        entry = self.audit.record(
            event_type="TOOL_OUTCOME",
            action=tool_name,
            decision="SUCCESS" if success else "FAILED",
            risk_level=risk_level.value,
            verified=verified,
            details={"summary": summary[:500]},
        )
        self._emit_audit(entry)

    def _evaluate_tool(
        self,
        tool_name: str,
        default_risk: RiskLevel,
        args: Dict[str, Any],
    ) -> PermissionCheckResult:
        """Apply specialized validators automatically based on tool semantics."""
        # 1. Application operations
        if tool_name in {"open_application", "open_app_project"}:
            return self.app_validator.check(str(args.get("app_name", "")))
        if tool_name == "close_application":
            app = str(args.get("app_name", ""))
            return PermissionCheckResult(
                PermissionDecision.REQUIRE_CONFIRMATION if args.get("force") else PermissionDecision.ALLOW,
                RiskLevel.SENSITIVE if args.get("force") else RiskLevel.MODERATE,
                f"Closing application '{app}'.",
                target_summary=app,
            )

        # 2. Terminal & script operations
        if tool_name == "run_terminal_command":
            cmd_check = self.cmd_validator.validate(str(args.get("command", "")))
            if cmd_check.decision == PermissionDecision.DENY:
                return cmd_check
            cwd = args.get("cwd")
            if cwd:
                _, dir_check = self.path_guard.check_read(self.resolve_tool_path(str(cwd)))
                if dir_check.decision == PermissionDecision.DENY:
                    return dir_check
            return cmd_check

        if tool_name == "run_python_script":
            script_path = self.resolve_tool_path(str(args.get("script_path", "")))
            _, pcheck = self.path_guard.check_read(script_path)
            if pcheck.decision == PermissionDecision.DENY:
                return pcheck
            return PermissionCheckResult(
                PermissionDecision.ALLOW,
                RiskLevel.MODERATE,
                f"Executing Python script '{script_path}' inside permitted directory.",
                target_summary=script_path,
            )

        # 3. Filesystem operations
        if tool_name in {"read_document", "list_directory", "find_duplicates", "inspect_blend_project", "analyze_error_log"}:
            path_val = self.resolve_tool_path(
                str(args.get("path") or args.get("directory") or args.get("blend_path") or args.get("log_path") or "")
            )
            _, pcheck = self.path_guard.check_read(path_val)
            return pcheck

        if tool_name == "search_files":
            directory = args.get("directory")
            if directory:
                _, pcheck = self.path_guard.check_read(self.resolve_tool_path(str(directory)), allow_drive_search=True)
                return pcheck
            return PermissionCheckResult(PermissionDecision.ALLOW, RiskLevel.SAFE, "Searching permitted roots.")

        if tool_name == "create_file":
            # Fail-closed: an LLM may set overwrite=true, but a human must still
            # approve clobbering an existing user file. `allow_overwrite` may only
            # be relaxed when the user explicitly disables the confirmation policy.
            _, pcheck = self.path_guard.check_write(
                self.resolve_tool_path(str(args.get("path", ""))),
                allow_overwrite=not self.cfg.require_confirmation_for_overwrite,
            )
            return pcheck

        if tool_name == "create_folder":
            _, pcheck = self.path_guard.check_write(
                self.resolve_tool_path(str(args.get("path", ""))),
                allow_overwrite=True,
            )
            return pcheck

        if tool_name == "delete_path":
            _, pcheck = self.path_guard.check_write(
                self.resolve_tool_path(str(args.get("path", ""))),
                is_delete=True,
            )
            return pcheck

        if tool_name in {"rename_path", "move_path", "copy_path"}:
            src = self.resolve_tool_path(str(args.get("source", "")))
            raw_dst = str(args.get("destination") or args.get("new_name") or "")
            src_path, scheck = self.path_guard.check_read(src)
            if scheck.decision == PermissionDecision.DENY:
                return scheck
            if tool_name == "rename_path" and raw_dst and not Path(raw_dst).is_absolute():
                dst = str(src_path.parent / raw_dst)
            else:
                dst = self.resolve_tool_path(raw_dst)
            _, dcheck = self.path_guard.check_write(dst, allow_overwrite=False)
            return dcheck

        if tool_name == "organize_directory":
            directory = self.resolve_tool_path(str(args.get("directory", "")))
            dry_run = bool(args.get("dry_run", False))
            if dry_run:
                _, pcheck = self.path_guard.check_read(directory)
                return pcheck
            target, pcheck = self.path_guard.check_read(directory)
            if pcheck.decision == PermissionDecision.DENY:
                return pcheck
            if not self.path_guard.is_within_permitted_dirs(target):
                return PermissionCheckResult(
                    PermissionDecision.DENY,
                    RiskLevel.BLOCKED,
                    f"Directory '{target}' is outside permitted directories.",
                    target_summary=str(target),
                )
            return PermissionCheckResult(
                PermissionDecision.ALLOW,
                RiskLevel.MODERATE,
                f"Organizing '{target}' with non-destructive collision-safe moves.",
                target_summary=str(target),
            )

        if tool_name == "open_blender_project":
            bp = self.resolve_tool_path(str(args.get("blend_path", "")))
            _, pcheck = self.path_guard.check_read(bp)
            if pcheck.decision == PermissionDecision.DENY:
                return pcheck
            return self.app_validator.check("blender")

        if tool_name == "open_vscode":
            proj = args.get("project_path")
            if proj:
                _, pcheck = self.path_guard.check_read(self.resolve_tool_path(str(proj)))
                if pcheck.decision == PermissionDecision.DENY:
                    return pcheck
            return self.app_validator.check("code")

        if tool_name == "take_screenshot" and args.get("output_path"):
            _, pcheck = self.path_guard.check_write(self.resolve_tool_path(str(args["output_path"])))
            return pcheck

        # 4. Process termination
        if tool_name == "stop_process":
            target_desc = str(args.get("name_or_pid", ""))
            decision = (
                PermissionDecision.REQUIRE_CONFIRMATION
                if self.cfg.require_confirmation_for_process_stop
                else PermissionDecision.ALLOW
            )
            return PermissionCheckResult(
                decision=decision,
                risk_level=RiskLevel.SENSITIVE,
                reason=f"Stopping process '{target_desc}' requires user confirmation.",
                target_summary=target_desc,
            )

        # 5. Browser operations
        if tool_name in {"open_website", "browser_navigate"}:
            return self.web_validator.check_url(str(args.get("url", "")))
        if tool_name in {"browser_click", "browser_fill"}:
            return self.web_validator.check_interaction(
                str(args.get("selector", "")),
                str(args.get("value", "")),
            )

        # 6. Generic risk-tier fallback
        if default_risk in {RiskLevel.SENSITIVE, RiskLevel.CRITICAL}:
            return PermissionCheckResult(
                PermissionDecision.REQUIRE_CONFIRMATION,
                default_risk,
                f"Sensitive operation '{tool_name}' requires explicit confirmation.",
                target_summary=tool_name,
            )
        if default_risk == RiskLevel.BLOCKED:
            return PermissionCheckResult(
                PermissionDecision.DENY,
                RiskLevel.BLOCKED,
                f"Operation '{tool_name}' is blocked by security policy.",
                target_summary=tool_name,
            )
        return PermissionCheckResult(
            PermissionDecision.ALLOW,
            default_risk,
            f"Permitted {default_risk.value} tool.",
            target_summary=tool_name,
        )

    # ---- Runtime Permission Management helpers for the GUI ----

    def add_permitted_directory(self, directory: str) -> str:
        resolved = Path(directory).expanduser().resolve()
        protected = self.path_guard._hits_protected_location(resolved)
        if protected:
            raise PermissionDeniedError(f"Cannot add {protected} to permitted directories.")
        norm = str(resolved)
        if norm not in self.cfg.permitted_directories:
            self.cfg.permitted_directories.append(norm)
            entry = self.audit.record(
                "POLICY_UPDATE", "add_permitted_directory", "UPDATED", "SENSITIVE", {"directory": norm}
            )
            self._emit_audit(entry)
        return norm

    def remove_permitted_directory(self, directory: str) -> bool:
        norm = str(Path(directory).expanduser().resolve())
        if norm in self.cfg.permitted_directories:
            self.cfg.permitted_directories.remove(norm)
            entry = self.audit.record(
                "POLICY_UPDATE", "remove_permitted_directory", "UPDATED", "SENSITIVE", {"directory": norm}
            )
            self._emit_audit(entry)
            return True
        return False

    def add_allowlisted_app(self, app_name: str) -> str:
        canonical = self.app_validator.normalize(app_name)
        if not canonical:
            raise ValueError("Application name cannot be empty.")
        if canonical not in self.cfg.allowlisted_apps:
            self.cfg.allowlisted_apps.append(canonical)
            entry = self.audit.record(
                "POLICY_UPDATE", "add_allowlisted_app", "UPDATED", "MODERATE", {"app": canonical}
            )
            self._emit_audit(entry)
        return canonical

    def remove_allowlisted_app(self, app_name: str) -> bool:
        canonical = self.app_validator.normalize(app_name)
        if canonical in self.cfg.allowlisted_apps:
            self.cfg.allowlisted_apps.remove(canonical)
            entry = self.audit.record(
                "POLICY_UPDATE", "remove_allowlisted_app", "UPDATED", "MODERATE", {"app": canonical}
            )
            self._emit_audit(entry)
            return True
        return False
