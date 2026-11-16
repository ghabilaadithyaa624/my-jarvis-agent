"""Model-agnostic Structured Output Parser & Anti-Hallucination Guard.

Never assumes a local Ollama model supports native OpenAI-style function calling.
Robustly extracts structured JSON tool actions across Llama, Qwen, Mistral, Gemma,
Phi, and DeepSeek-R1 (`<think>...</think>`) models, and prevents unverified claims.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)
_TOOL_CALL_XML_RE = re.compile(r"<tool_call>\s*([\s\S]*?)\s*</tool_call>", re.IGNORECASE)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([\}\]])")

# Detects when an LLM claims it performed a desktop action without calling any tool
_UNVERIFIED_CLAIM_RE = re.compile(
    r"(?i)\b(i have (opened|launched|closed|created|deleted|removed|moved|renamed|copied|"
    r"organized|executed|ran|run|stopped|terminated|set the volume|taken a screenshot|started a timer)|"
    r"i've (opened|launched|closed|created|deleted|moved|organized|executed|stopped|set)|"
    r"successfully (opened|launched|closed|created|deleted|moved|organized|executed|terminated))\b"
)


@dataclass
class ParsedAgentStep:
    kind: str  # "tool_call" | "final_answer" | "parse_error"
    thought: str = ""
    plan: List[str] = field(default_factory=list)
    tool_name: str = ""
    tool_args: Dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    error: str = ""
    raw_text: str = ""


class StructuredOutputParser:
    """Extracts validated agent decisions from arbitrary local LLM output formats."""

    def parse(self, raw_text: str, valid_tools: Optional[set[str]] = None) -> ParsedAgentStep:
        text = (raw_text or "").strip()
        if not text:
            return ParsedAgentStep(kind="parse_error", error="Model returned empty output.", raw_text=raw_text)

        # Extract optional <think> reasoning trace (DeepSeek-R1 / Qwen3)
        thoughts: List[str] = [m.group(1).strip() for m in _THINK_RE.finditer(text)]
        cleaned = _THINK_RE.sub("", text).strip()

        # Most specific structured rejection seen so far (e.g. "Unknown tool 'x'").
        # Small models often emit several JSON-ish fragments; the informative error
        # from the best candidate must not be swallowed by a later, less useful one.
        best_rejection: Optional[ParsedAgentStep] = None

        def _consider(step: ParsedAgentStep) -> Optional[ParsedAgentStep]:
            nonlocal best_rejection
            if step.kind != "parse_error":
                return step
            if best_rejection is None or (not best_rejection.error and step.error):
                best_rejection = step
            return None

        # Check Qwen/Hermes <tool_call>{...}</tool_call> wrapper first
        xml_match = _TOOL_CALL_XML_RE.search(cleaned)
        if xml_match:
            obj = self._try_load_json(xml_match.group(1))
            if isinstance(obj, dict):
                accepted = _consider(self._interpret_dict(obj, thoughts, raw_text, valid_tools))
                if accepted is not None:
                    return accepted

        # Try direct JSON, fenced JSON, and balanced brace extraction
        candidates: List[str] = [cleaned]
        for fm in _FENCED_JSON_RE.finditer(cleaned):
            candidates.append(fm.group(1).strip())
        balanced = self._extract_first_json_object(cleaned)
        if balanced:
            candidates.append(balanced)

        for cand in candidates:
            obj = self._try_load_json(cand)
            if isinstance(obj, dict):
                accepted = _consider(self._interpret_dict(obj, thoughts, raw_text, valid_tools))
                if accepted is not None:
                    return accepted

        if best_rejection is not None and best_rejection.error:
            return best_rejection

        # If model answered in plain prose without JSON envelope, return parse_error so
        # the executor can decide whether to re-prompt for JSON or treat as answer.
        return ParsedAgentStep(
            kind="parse_error",
            thought="\n".join(thoughts).strip(),
            answer=cleaned,
            error="Output was not valid structured JSON.",
            raw_text=raw_text,
        )

    def _interpret_dict(
        self,
        obj: Dict[str, Any],
        extra_thoughts: List[str],
        raw_text: str,
        valid_tools: Optional[set[str]],
    ) -> ParsedAgentStep:
        thought = str(obj.get("thought") or obj.get("reasoning") or "").strip()
        if extra_thoughts:
            thought = ("\n".join(extra_thoughts) + ("\n" + thought if thought else "")).strip()

        raw_plan = obj.get("plan")
        plan = [str(s).strip() for s in raw_plan if str(s).strip()] if isinstance(raw_plan, list) else []

        action = str(
            obj.get("action") or obj.get("tool") or obj.get("name") or obj.get("mode") or ""
        ).strip()
        args = obj.get("args") if "args" in obj else (obj.get("arguments") or obj.get("parameters") or {})
        if isinstance(args, str):
            parsed_args = self._try_load_json(args)
            args = parsed_args if isinstance(parsed_args, dict) else {}
        if not isinstance(args, dict):
            args = {}

        answer = str(
            obj.get("answer") or obj.get("response") or obj.get("final_answer") or obj.get("message") or ""
        ).strip()

        if action.lower() in {"final_answer", "final", "respond", "answer", "none", "done"}:
            if not answer:
                answer = thought or "Task finished."
            return ParsedAgentStep(
                kind="final_answer",
                thought=thought,
                plan=plan,
                answer=answer,
                raw_text=raw_text,
            )

        # Case where model returned {"tool": "open_vscode", ...} or {"action": "tool_call", "tool_name": "..."}
        if action.lower() == "tool_call":
            action = str(obj.get("tool_name") or obj.get("tool") or obj.get("name") or "").strip()

        if action:
            if valid_tools is not None and action not in valid_tools:
                return ParsedAgentStep(
                    kind="parse_error",
                    thought=thought,
                    plan=plan,
                    tool_name=action,
                    error=f"Unknown tool '{action}'. Only use listed tools or action='final_answer'.",
                    raw_text=raw_text,
                )
            return ParsedAgentStep(
                kind="tool_call",
                thought=thought,
                plan=plan,
                tool_name=action,
                tool_args=args,
                raw_text=raw_text,
            )

        if answer:
            return ParsedAgentStep(
                kind="final_answer",
                thought=thought,
                plan=plan,
                answer=answer,
                raw_text=raw_text,
            )

        return ParsedAgentStep(
            kind="parse_error",
            thought=thought,
            error="JSON object missing 'action' (tool name or 'final_answer').",
            raw_text=raw_text,
        )

    @staticmethod
    def looks_like_unverified_action_claim(answer: str) -> bool:
        """Detect when an LLM pretends it executed a desktop command without running any tool."""
        return bool(_UNVERIFIED_CLAIM_RE.search(answer or ""))

    @staticmethod
    def _try_load_json(snippet: str) -> Optional[Any]:
        s = snippet.strip()
        if not s or s[0] not in "{[":
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            # Repair trailing commas commonly produced by small local models
            repaired = _TRAILING_COMMA_RE.sub(r"\1", s)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                return None

    @staticmethod
    def _extract_first_json_object(text: str) -> Optional[str]:
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
        return None
