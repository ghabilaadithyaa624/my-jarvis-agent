"""Central ToolRegistry enforcing schema validation, SecurityGate, timeout & audit logging."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, Iterable, List, Optional

from jarvis.logs.logger import get_logger
from jarvis.security.emergency import CancellationToken, TaskCancelledError
from jarvis.security.gate import PermissionDeniedError, SecurityGate
from jarvis.tools.base import ToolResult, ToolSpec

log = get_logger("tools.registry")


class ToolRegistry:
    """Stores all registered desktop operator tools and executes them safely."""

    def __init__(self, security_gate: SecurityGate) -> None:
        self.security = security_gate
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_names(self) -> List[str]:
        return sorted(self._tools.keys())

    def list_specs(self, categories: Optional[Iterable[str]] = None) -> List[ToolSpec]:
        if not categories:
            return [self._tools[k] for k in sorted(self._tools)]
        allowed = set(categories)
        return [
            self._tools[k]
            for k in sorted(self._tools)
            if self._tools[k].category in allowed
        ]

    def format_prompt_catalog(self, categories: Optional[Iterable[str]] = None) -> str:
        """Render concise tool definitions grouped by category for the local LLM."""
        specs = self.list_specs(categories)
        lines: List[str] = []
        for spec in specs:
            lines.append(spec.compact_signature())
        return "\n".join(lines)

    def execute(
        self,
        tool_name: str,
        raw_args: Optional[Dict[str, Any]] = None,
        *,
        timeout_sec: float = 45.0,
        token: Optional[CancellationToken] = None,
    ) -> ToolResult:
        """Validate arguments -> check SecurityGate -> run with timeout -> verify result."""
        started = time.perf_counter()
        spec = self._tools.get((tool_name or "").strip())
        if spec is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                verified=True,
                output="",
                error=f"Unknown tool '{tool_name}'. Choose one of: {', '.join(self.list_names()[:20])}...",
                duration_ms=0.0,
            )

        args, validation_err = spec.validate_and_coerce(raw_args)
        if validation_err:
            return ToolResult(
                tool_name=spec.name,
                success=False,
                verified=True,
                output="",
                error=validation_err,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )

        # Mandatory security gate check (including PathGuard, CommandValidator, AppAllowlist & Confirmation)
        try:
            check = self.security.authorize(
                tool_name=spec.name,
                default_risk=spec.risk_level,
                arguments=args,
                token=token,
            )
        except PermissionDeniedError as exc:
            return ToolResult(
                tool_name=spec.name,
                success=False,
                verified=True,
                output="",
                error=str(exc),
                permission_denied=True,
                verification_detail="Blocked by SecurityGate.",
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
        except TaskCancelledError:
            raise

        # Execute tool inside bounded timeout wrapper
        def _invoke() -> Dict[str, Any]:
            if spec.accepts_token:
                return spec.handler(args, token)
            return spec.handler(args, None)

        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{spec.name}") as pool:
                fut = pool.submit(_invoke)
                data = fut.result(timeout=max(2.0, timeout_sec))
        except TaskCancelledError:
            raise
        except FuturesTimeoutError:
            if token:
                token.cancel(f"Tool '{spec.name}' timed out after {timeout_sec}s.")
            elapsed = round((time.perf_counter() - started) * 1000.0, 1)
            self.security.record_outcome(
                spec.name, check.risk_level, False, False, f"Timed out after {timeout_sec}s"
            )
            return ToolResult(
                tool_name=spec.name,
                success=False,
                verified=False,
                output="",
                error=f"Tool '{spec.name}' exceeded timeout ({timeout_sec}s).",
                duration_ms=elapsed,
            )
        except Exception as exc:
            log.exception("Unhandled error in tool %s", spec.name)
            elapsed = round((time.perf_counter() - started) * 1000.0, 1)
            self.security.record_outcome(spec.name, check.risk_level, False, False, str(exc))
            return ToolResult(
                tool_name=spec.name,
                success=False,
                verified=False,
                output="",
                error=f"Tool execution error: {exc}",
                duration_ms=elapsed,
            )

        if not isinstance(data, dict):
            data = {"result": data}

        success = bool(data.get("success", True))
        verified = bool(data.get("verified", False))
        error = data.get("error")
        output = data.get("formatted_output") or json.dumps(data, indent=2, ensure_ascii=False, default=str)
        ver_detail = str(
            data.get("verification_detail")
            or ("Outcome verified against system state." if verified else "Outcome could not be independently verified.")
        )
        elapsed = round((time.perf_counter() - started) * 1000.0, 1)

        self.security.record_outcome(
            spec.name,
            check.risk_level,
            success,
            verified,
            error or output[:240],
        )

        return ToolResult(
            tool_name=spec.name,
            success=success,
            verified=verified,
            output=output,
            data=data,
            error=str(error) if error else None,
            verification_detail=ver_detail,
            duration_ms=elapsed,
        )
