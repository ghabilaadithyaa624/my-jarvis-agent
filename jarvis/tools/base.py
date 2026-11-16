"""Typed tool contracts and strict model-independent argument validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from jarvis.security.emergency import CancellationToken
from jarvis.security.policy import RiskLevel


@dataclass
class ToolParam:
    name: str
    param_type: str  # string | integer | number | boolean | array
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    verified: bool
    output: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    verification_detail: str = ""
    duration_ms: float = 0.0
    permission_denied: bool = False

    def to_observation(self, max_chars: int = 2400) -> str:
        """Compact, truthful tool observation string fed back into the agent loop."""
        status = "SUCCESS" if self.success else ("PERMISSION_DENIED" if self.permission_denied else "FAILED")
        ver = "VERIFIED" if self.verified else "UNVERIFIED"
        body = self.output or (self.error or "")
        if len(body) > max_chars:
            body = body[:max_chars] + "\n...[truncated]"
        header = f"[Tool={self.tool_name} | Status={status} | Verification={ver}]"
        if self.error and self.error not in body:
            return f"{header}\nError: {self.error}\n{body}".strip()
        return f"{header}\n{body}".strip()


ToolHandler = Callable[[Dict[str, Any], Optional[CancellationToken]], Dict[str, Any]]


@dataclass
class ToolSpec:
    name: str
    category: str  # application | filesystem | system | browser | developer | utilities | memory
    description: str
    parameters: List[ToolParam]
    risk_level: RiskLevel
    handler: ToolHandler
    accepts_token: bool = False

    def validate_and_coerce(self, raw_args: Any) -> Tuple[Dict[str, Any], Optional[str]]:
        """Validate & coerce LLM-supplied arguments independently of model quality."""
        if raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, dict):
            return {}, f"Arguments for '{self.name}' must be a JSON object, got {type(raw_args).__name__}."

        cleaned: Dict[str, Any] = {}
        for p in self.parameters:
            if p.name not in raw_args or raw_args[p.name] is None:
                if p.required:
                    return {}, f"Missing required parameter '{p.name}' ({p.param_type}) for tool '{self.name}'."
                if p.default is not None:
                    cleaned[p.name] = p.default
                continue

            val = raw_args[p.name]
            try:
                coerced = self._coerce_value(val, p.param_type)
            except ValueError as exc:
                return {}, f"Invalid parameter '{p.name}' for '{self.name}': {exc}"

            if p.enum and str(coerced) not in p.enum:
                return {}, f"Parameter '{p.name}' must be one of {p.enum}, got '{coerced}'."
            cleaned[p.name] = coerced

        return cleaned, None

    @staticmethod
    def _coerce_value(val: Any, ptype: str) -> Any:
        if ptype == "string":
            return str(val)
        if ptype == "integer":
            if isinstance(val, bool):
                raise ValueError("expected integer, got boolean")
            return int(float(val)) if isinstance(val, str) else int(val)
        if ptype == "number":
            if isinstance(val, bool):
                raise ValueError("expected number, got boolean")
            return float(val)
        if ptype == "boolean":
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                low = val.strip().lower()
                if low in {"true", "1", "yes", "on"}:
                    return True
                if low in {"false", "0", "no", "off"}:
                    return False
            raise ValueError(f"cannot convert '{val}' to boolean")
        if ptype == "array":
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    return [val]
            raise ValueError("expected JSON array/list")
        return val

    def compact_signature(self) -> str:
        """One-line token-efficient schema ideal for 7B/8B local models in 4K context."""
        arg_parts: List[str] = []
        for p in self.parameters:
            req = "" if p.required else "?"
            enum_hint = f"[{'|'.join(p.enum)}]" if p.enum else p.param_type
            arg_parts.append(f"{p.name}{req}: {enum_hint}")
        args_str = ", ".join(arg_parts)
        return f"- {self.name}({args_str}) — {self.description}"
