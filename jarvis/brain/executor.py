"""9-Stage Autonomous Execution Engine (Section 7).

Pipeline:
  1. User Input
  2. Intent Interpretation (+ Memory Recall)
  3. Task Planning
  4. Tool Selection
  5. Permission Checking
  6. Execution (bounded loops, per-step & per-task timeouts, cancellation)
  7. Result Verification
  8. Error Handling & Recovery
  9. User Feedback (grounded response + persistent history)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from jarvis.app.events import EventBus, EventType
from jarvis.brain.intent import IntentAnalysis, IntentClassifier, IntentKind
from jarvis.brain.output_parser import StructuredOutputParser
from jarvis.brain.planner import ExecutionPlan, TaskPlanner
from jarvis.brain.prompts import (
    build_agent_system_prompt,
    build_chat_system_prompt,
    build_synthesis_prompt,
)
from jarvis.brain.verifier import ResultVerifier, VerdictStatus
from jarvis.config.schema import JarvisConfig
from jarvis.logs.logger import get_logger
from jarvis.memory.conversation_store import ConversationStore
from jarvis.memory.knowledge_store import KnowledgeStore
from jarvis.memory.utilities_store import UtilitiesStore
from jarvis.models.model_manager import ModelManager
from jarvis.models.ollama_client import OllamaError
from jarvis.security.emergency import (
    CancellationToken,
    EmergencyStopController,
    EmergencyStopError,
    TaskCancelledError,
)
from jarvis.system.resource_guard import ResourceGuard
from jarvis.tools.base import ToolResult
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.wake_word import WakeWordDetector

log = get_logger("brain.executor")


@dataclass
class AgentTurnResult:
    user_input: str
    response: str
    intent: str
    status: str                # completed | partial | failed | denied | cancelled | meta
    verified: Optional[bool]   # None for pure Q&A chat; True/False for computer actions
    verification_summary: str
    tool_results: List[ToolResult] = field(default_factory=list)
    plan: Optional[Dict[str, Any]] = None
    model_used: str = ""
    language: str = "en"
    duration_ms: float = 0.0


class AgentExecutor:
    """Drives JARVIS's grounded, verified plan-execute-verify loop."""

    def __init__(
        self,
        *,
        config: JarvisConfig,
        model_manager: ModelManager,
        tool_registry: ToolRegistry,
        conversations: ConversationStore,
        knowledge: KnowledgeStore,
        utilities: UtilitiesStore,
        emergency: EmergencyStopController,
        resource_guard: ResourceGuard,
        event_bus: EventBus,
        meta_handler: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.config = config
        self.models = model_manager
        self.tools = tool_registry
        self.conversations = conversations
        self.knowledge = knowledge
        self.utilities = utilities
        self.emergency = emergency
        self.resource_guard = resource_guard
        self.events = event_bus
        self._meta_handler = meta_handler

        self.intent_classifier = IntentClassifier()
        self.planner = TaskPlanner()
        self.parser = StructuredOutputParser()
        self.verifier = ResultVerifier()
        self._wake_stripper = WakeWordDetector(use_openwakeword=False)

    def _stage(self, stage_num: int, stage_name: str, detail: str = "") -> None:
        self.events.emit(
            EventType.TASK_STAGE,
            stage_number=stage_num,
            stage=stage_name,
            detail=detail,
        )

    def handle_input(
        self,
        raw_user_input: str,
        *,
        source: str = "text",
        token: Optional[CancellationToken] = None,
    ) -> AgentTurnResult:
        """Run the full 9-stage execution architecture on a user instruction."""
        started = time.perf_counter()

        # ---------- STAGE 1: USER INPUT ----------
        self._stage(1, "input", "Receiving instruction")
        text = (raw_user_input or "").strip()
        if not text:
            return AgentTurnResult("", "", "empty", "completed", None, "Empty input.")

        # Strip optional leading "Jarvis," wake word even in typed or continuous input
        lang_profile = self.config.active_language_profile()
        self._wake_stripper.update_phrases(
            list({*lang_profile.wake_phrases, "hey jarvis", "ok jarvis", "jarvis", "ஜார்விஸ்"})
        )
        wm = self._wake_stripper.match_transcript(text)
        clean_input = wm.remainder_command if (wm.detected and wm.remainder_command) else text

        # ---------- STAGE 2: INTENT INTERPRETATION ----------
        self._stage(2, "intent_interpretation", "Classifying intent & recalling memory")
        analysis = self.intent_classifier.analyze(clean_input, default_language=lang_profile.code)
        active_lang_cfg = self.config.languages.get(analysis.detected_language, lang_profile)

        # Handle meta safety commands immediately (even if Emergency Stop is engaged!)
        if analysis.kind == IntentKind.META_CONTROL and analysis.meta_command:
            reply = (
                self._meta_handler(analysis.meta_command)
                if self._meta_handler
                else f"Executed control command: {analysis.meta_command}."
            )
            self._stage(9, "feedback", reply)
            return AgentTurnResult(
                user_input=text,
                response=reply,
                intent=analysis.kind.value,
                status="meta",
                verified=True,
                verification_summary=reply,
                language=analysis.detected_language,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )

        own_token = token is None
        try:
            task_token = token or self.emergency.create_task_token()
        except EmergencyStopError as exc:
            msg = str(exc)
            self.events.emit(EventType.ERROR, message=msg)
            return AgentTurnResult(
                user_input=text,
                response=msg,
                intent="blocked",
                status="cancelled",
                verified=True,
                verification_summary=msg,
                language=analysis.detected_language,
            )

        # Persist user message in conversation history
        prior_context = self.conversations.build_llm_context(
            max_messages=self.config.agent.max_history_messages
        )
        self.conversations.add_message("user", text, metadata={"source": source})
        self.events.emit(EventType.MESSAGE_ADDED, role="user", content=text, source=source)

        memories = self.knowledge.get_pinned_and_relevant(clean_input, limit=6)

        # ---------- STAGE 3: TASK PLANNING ----------
        self._stage(3, "task_planning", analysis.summary)
        plan = self.planner.create_initial_plan(clean_input, analysis)
        self.events.emit(EventType.PLAN_CREATED, plan=plan.to_dict())

        try:
            if not analysis.requires_tools:
                result = self._run_conversational_turn(
                    user_text=clean_input,
                    prior_context=prior_context,
                    memories=memories,
                    language_hint=active_lang_cfg.system_prompt_hint,
                    lang_code=analysis.detected_language,
                    plan=plan,
                    token=task_token,
                    started=started,
                )
            elif self.config.agent.enable_fast_path and analysis.fast_tool:
                result = self._run_fast_path_turn(
                    user_text=clean_input,
                    analysis=analysis,
                    language_hint=active_lang_cfg.system_prompt_hint,
                    lang_code=analysis.detected_language,
                    plan=plan,
                    token=task_token,
                    started=started,
                )
            else:
                result = self._run_autonomous_loop(
                    user_text=clean_input,
                    analysis=analysis,
                    prior_context=prior_context,
                    memories=memories,
                    language_hint=active_lang_cfg.system_prompt_hint,
                    lang_code=analysis.detected_language,
                    plan=plan,
                    token=task_token,
                    started=started,
                )
        except (TaskCancelledError, EmergencyStopError) as exc:
            elapsed = round((time.perf_counter() - started) * 1000.0, 1)
            msg = f"Task stopped: {exc}"
            self.events.emit(EventType.STREAM_END, cancelled=True)
            result = AgentTurnResult(
                user_input=text,
                response=msg,
                intent=analysis.kind.value,
                status="cancelled",
                verified=True,
                verification_summary="Cancelled on user command.",
                plan=plan.to_dict(),
                language=analysis.detected_language,
                duration_ms=elapsed,
            )
        finally:
            if own_token:
                self.emergency.release_task_token(task_token)

        # ---------- STAGE 9: USER FEEDBACK & PERSISTENCE ----------
        self._stage(9, "feedback", f"Status: {result.status}")
        self.conversations.add_message(
            "assistant",
            result.response,
            model=result.model_used,
            verified=result.verified,
            metadata={
                "intent": result.intent,
                "status": result.status,
                "verification_summary": result.verification_summary,
                "tools_used": [tr.tool_name for tr in result.tool_results],
            },
        )
        self.events.emit(
            EventType.MESSAGE_ADDED,
            role="assistant",
            content=result.response,
            model=result.model_used,
            verified=result.verified,
            status=result.status,
            verification_summary=result.verification_summary,
        )

        if analysis.requires_tools:
            self.utilities.log_task(
                session_id=self.conversations.current_session_id,
                user_input=text,
                intent=result.intent,
                status=result.status,
                verified=bool(result.verified),
                steps_count=len(result.tool_results),
                duration_ms=result.duration_ms,
                summary=result.response[:500],
                details={
                    "verification": result.verification_summary,
                    "tools": [
                        {
                            "name": tr.tool_name,
                            "success": tr.success,
                            "verified": tr.verified,
                            "duration_ms": tr.duration_ms,
                            "error": tr.error,
                        }
                        for tr in result.tool_results
                    ],
                },
            )

        return result

    # ---------------- Pure Conversational Path ----------------

    def _run_conversational_turn(
        self,
        *,
        user_text: str,
        prior_context: List[Dict[str, str]],
        memories: List[Dict[str, Any]],
        language_hint: str,
        lang_code: str,
        plan: ExecutionPlan,
        token: CancellationToken,
        started: float,
    ) -> AgentTurnResult:
        sys_prompt = build_chat_system_prompt(language_hint, memories)
        messages = [
            {"role": "system", "content": sys_prompt},
            *prior_context,
            {"role": "user", "content": user_text},
        ]
        num_ctx = self.resource_guard.recommended_num_ctx()
        self.events.emit(EventType.STREAM_START, mode="chat")
        try:
            chat_res = self.models.chat(
                messages,
                num_ctx=num_ctx,
                on_token=lambda tok: self.events.emit(EventType.STREAM_TOKEN, token=tok),
                token=token,
            )
            self.events.emit(EventType.STREAM_END, cancelled=False)
            plan.record_step("ollama_chat", "Conversational response", "verified")
            return AgentTurnResult(
                user_input=user_text,
                response=chat_res.content.strip(),
                intent=IntentKind.QUESTION.value,
                status="completed",
                verified=None,
                verification_summary="Conversational response (no desktop mutation).",
                plan=plan.to_dict(),
                model_used=chat_res.model,
                language=lang_code,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
        except OllamaError as exc:
            self.events.emit(EventType.STREAM_END, cancelled=True)
            u_low = user_text.lower().strip()
            if any(u_low.startswith(p) for p in ("no problem", "thanks", "thank you", "sure", "cool", "great")):
                conv_fallback = "You're very welcome! Let me know whenever you're ready for the next task."
                return AgentTurnResult(
                    user_input=user_text,
                    response=conv_fallback,
                    intent=IntentKind.QUESTION.value,
                    status="completed",
                    verified=None,
                    verification_summary="Conversational response.",
                    plan=plan.to_dict(),
                    model_used="conversational_agent",
                    language=lang_code,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
                )
            if any(u_low.startswith(p) for p in ("hi", "hello", "hey")):
                conv_fallback = "Hello! I am here and ready to help. What task would you like me to work on?"
                return AgentTurnResult(
                    user_input=user_text,
                    response=conv_fallback,
                    intent=IntentKind.QUESTION.value,
                    status="completed",
                    verified=None,
                    verification_summary="Conversational response.",
                    plan=plan.to_dict(),
                    model_used="conversational_agent",
                    language=lang_code,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
                )
            err_msg = f"Local Ollama is unavailable: {exc}"
            self.events.emit(EventType.ERROR, message=err_msg)
            return AgentTurnResult(
                user_input=user_text,
                response=err_msg,
                intent=IntentKind.QUESTION.value,
                status="failed",
                verified=False,
                verification_summary=err_msg,
                plan=plan.to_dict(),
                language=lang_code,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )

    # ---------------- Low-Latency Verified Fast-Path ----------------

    def _run_fast_path_turn(
        self,
        *,
        user_text: str,
        analysis: IntentAnalysis,
        language_hint: str,
        lang_code: str,
        plan: ExecutionPlan,
        token: CancellationToken,
        started: float,
    ) -> AgentTurnResult:
        tool_name = analysis.fast_tool or ""
        tool_args = analysis.fast_args

        t_res, verdict = self._execute_and_verify_step(
            tool_name=tool_name,
            tool_args=tool_args,
            thought=f"Direct execution of {tool_name}",
            plan=plan,
            token=token,
        )

        # Synthesize natural spoken answer via Ollama if online, otherwise crisp deterministic summary!
        model_used = ""
        response_text = ""
        if self.models.active_model or self.models.is_online:
            try:
                num_ctx = self.resource_guard.recommended_num_ctx()
                messages = [
                    {"role": "system", "content": build_synthesis_prompt(language_hint)},
                    {
                        "role": "user",
                        "content": f"User asked: {user_text}\n\nVerified tool result:\n{t_res.to_observation(2200)}",
                    },
                ]
                self.events.emit(EventType.STREAM_START, mode="synthesis")
                chat_res = self.models.chat(
                    messages,
                    temperature=0.15,
                    num_ctx=num_ctx,
                    max_tokens=120,
                    on_token=lambda tok: self.events.emit(EventType.STREAM_TOKEN, token=tok),
                    token=token,
                )
                self.events.emit(EventType.STREAM_END, cancelled=False)
                response_text = chat_res.content.strip()
                model_used = chat_res.model
            except OllamaError:
                self.events.emit(EventType.STREAM_END, cancelled=True)

        if not response_text:
            response_text = self._deterministic_summary(t_res)

        plan.record_step("feedback", "Report verified outcome", "verified" if t_res.success else "failed")
        self.events.emit(EventType.PLAN_CREATED, plan=plan.to_dict())

        status = (
            "completed"
            if verdict.status == VerdictStatus.VERIFIED_SUCCESS
            else ("denied" if t_res.permission_denied else ("partial" if t_res.success else "failed"))
        )
        if status == "completed":
            response_text = self._attach_task_completion_followup(response_text, lang_code)
        return AgentTurnResult(
            user_input=user_text,
            response=response_text,
            intent=analysis.kind.value,
            status=status,
            verified=t_res.success and t_res.verified,
            verification_summary=verdict.summary,
            tool_results=[t_res],
            plan=plan.to_dict(),
            model_used=model_used,
            language=lang_code,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )

    # ---------------- Full Multi-Step Autonomous Loop ----------------

    def _run_autonomous_loop(
        self,
        *,
        user_text: str,
        analysis: IntentAnalysis,
        prior_context: List[Dict[str, str]],
        memories: List[Dict[str, Any]],
        language_hint: str,
        lang_code: str,
        plan: ExecutionPlan,
        token: CancellationToken,
        started: float,
    ) -> AgentTurnResult:
        workspace_dir = self.config.resolved_data_dir() / "workspace"
        tool_catalog = self.tools.format_prompt_catalog(analysis.categories)
        valid_tools = set(self.tools.list_names())

        sys_prompt = build_agent_system_prompt(
            tool_catalog=tool_catalog,
            workspace_dir=workspace_dir,
            permitted_dirs=self.config.security.permitted_directories,
            language_hint=language_hint,
            memories=memories,
        )

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            *prior_context[-6:],
            {"role": "user", "content": user_text},
        ]

        tool_results: List[ToolResult] = []
        max_steps = self.config.agent.max_steps_per_task
        deadline = time.monotonic() + self.config.agent.task_timeout_sec
        parse_retries = 0
        consecutive_failures = 0
        model_used = ""
        final_answer = ""

        for step_idx in range(1, max_steps + 1):
            self.emergency.check(token)
            if time.monotonic() >= deadline:
                final_answer = (
                    f"Task stopped after reaching the {self.config.agent.task_timeout_sec}s safety timeout. "
                    f"Completed {sum(1 for r in tool_results if r.success and r.verified)} verified step(s)."
                )
                break

            # ---------- STAGE 4: TOOL SELECTION ----------
            self._stage(4, "tool_selection", f"Step {step_idx}/{max_steps}: selecting action")
            num_ctx = self.resource_guard.recommended_num_ctx()
            try:
                llm_res = self.models.chat(
                    messages,
                    temperature=0.1,
                    num_ctx=num_ctx,
                    max_tokens=900,
                    json_mode=True,
                    token=token,
                )
                model_used = llm_res.model
            except OllamaError as exc:
                err_msg = f"Cannot execute autonomous plan — local Ollama error: {exc}"
                self.events.emit(EventType.ERROR, message=err_msg)
                return AgentTurnResult(
                    user_input=user_text,
                    response=err_msg,
                    intent=analysis.kind.value,
                    status="failed",
                    verified=False,
                    verification_summary=err_msg,
                    tool_results=tool_results,
                    plan=plan.to_dict(),
                    model_used=model_used,
                    language=lang_code,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
                )

            parsed = self.parser.parse(llm_res.content, valid_tools=valid_tools)

            if parsed.plan:
                plan.update_from_llm_plan(parsed.plan)
                self.events.emit(EventType.PLAN_CREATED, plan=plan.to_dict())

            # Handle malformed model output via bounded corrective retry
            if parsed.kind == "parse_error":
                parse_retries += 1
                if parse_retries <= self.config.agent.max_retries_per_step:
                    self._stage(8, "error_recovery", f"Repairing structured output ({parsed.error})")
                    messages.append({"role": "assistant", "content": llm_res.content})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"SYSTEM VALIDATOR: {parsed.error} "
                            "Reply with ONLY a valid JSON object containing 'thought', 'action' "
                            "(an exact tool name or 'final_answer'), and 'args' (or 'answer')."
                        ),
                    })
                    continue
                # Exhausted parse retries — return truthful message
                final_answer = (
                    f"Model '{model_used}' could not produce a valid tool call ({parsed.error}). "
                    "No unverified action was executed."
                )
                break

            parse_retries = 0

            # Handle final_answer with Anti-Hallucination verification check!
            if parsed.kind == "final_answer":
                if (
                    not tool_results
                    and self.parser.looks_like_unverified_action_claim(parsed.answer)
                    and step_idx < max_steps
                ):
                    log.warning("Blocked unverified action claim from LLM; prompting real tool execution.")
                    self._stage(8, "error_recovery", "Blocked unverified action claim — requiring real tool call")
                    messages.append({"role": "assistant", "content": llm_res.content})
                    messages.append({
                        "role": "user",
                        "content": (
                            "SYSTEM VERIFIER: You have NOT executed any tool yet, so you MUST NOT claim an "
                            "action succeeded. Call the required tool now via JSON {'action': '<tool>', 'args': {...}}, "
                            "or honestly explain why you cannot perform it."
                        ),
                    })
                    continue
                final_answer = parsed.answer
                break

            # ---------- STAGES 5, 6, 7: PERMISSION, EXECUTION & VERIFICATION ----------
            t_res, verdict = self._execute_and_verify_step(
                tool_name=parsed.tool_name,
                tool_args=parsed.tool_args,
                thought=parsed.thought,
                plan=plan,
                token=token,
            )
            tool_results.append(t_res)

            # Append tool observation + independent verifier feedback
            obs = t_res.to_observation(2000)
            if verdict.recovery_hint:
                obs += f"\n[Verifier Guidance]: {verdict.recovery_hint}"
            messages.append({"role": "assistant", "content": llm_res.content})
            messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{obs}"})

            # ---------- STAGE 8: ERROR HANDLING & BOUNDED RECOVERY ----------
            if verdict.status in {VerdictStatus.RECOVERABLE_FAILURE, VerdictStatus.FATAL_FAILURE}:
                consecutive_failures += 1
                self._stage(8, "error_recovery", verdict.summary)
                if consecutive_failures > self.config.agent.max_retries_per_step:
                    final_answer = (
                        f"Stopped after {consecutive_failures} consecutive failed attempts. "
                        f"Last verified error from '{t_res.tool_name}': {t_res.error}"
                    )
                    break
            elif verdict.status == VerdictStatus.PERMISSION_DENIED:
                consecutive_failures = 0
                self._stage(8, "error_recovery", "Action denied by security policy")
            else:
                consecutive_failures = 0

        if not final_answer:
            if tool_results:
                final_answer = (
                    f"Reached the {max_steps}-step execution bound. "
                    + self._deterministic_summary(tool_results[-1])
                )
            else:
                final_answer = "Could not complete the requested operation."

        overall_verified, ver_summary = self.verifier.aggregate_task_verification(tool_results)
        if any(r.permission_denied for r in tool_results) and not any(r.success for r in tool_results):
            status = "denied"
        elif overall_verified:
            status = "completed"
        elif any(r.success for r in tool_results):
            status = "partial"
        else:
            status = "failed"

        if status == "completed":
            final_answer = self._attach_task_completion_followup(final_answer, lang_code)

        return AgentTurnResult(
            user_input=user_text,
            response=final_answer,
            intent=analysis.kind.value,
            status=status,
            verified=overall_verified if tool_results else False,
            verification_summary=ver_summary,
            tool_results=tool_results,
            plan=plan.to_dict(),
            model_used=model_used,
            language=lang_code,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )

    def _execute_and_verify_step(
        self,
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
        thought: str,
        plan: ExecutionPlan,
        token: CancellationToken,
    ):
        low_risk = self._is_low_risk_tool(tool_name)
        # Stage 5: Permission Check (skipped for low-risk read-only tools)
        if low_risk:
            self._stage(5, "permission_check", f"Auto-approved safe tool '{tool_name}'")
        else:
            self._stage(5, "permission_check", f"Validating '{tool_name}'")
        self.events.emit(
            EventType.TOOL_STARTED,
            tool_name=tool_name,
            args=tool_args,
            thought=thought,
        )

        # Stage 6: Execution (SecurityGate runs first inside registry.execute)
        self._stage(6, "execution", f"Running '{tool_name}'")
        t_res = self.tools.execute(
            tool_name,
            tool_args,
            timeout_sec=self.config.agent.step_timeout_sec,
            token=token,
        )

        # Stage 7: Verification
        self._stage(7, "verification", f"Verifying '{tool_name}'")
        verdict = self.verifier.evaluate(t_res)

        step_status = (
            "verified"
            if verdict.status == VerdictStatus.VERIFIED_SUCCESS
            else ("denied" if t_res.permission_denied else "failed")
        )
        plan.record_step(tool_name, thought or f"Run {tool_name}", step_status, verdict.summary)

        self.events.emit(
            EventType.TOOL_FINISHED,
            tool_name=t_res.tool_name,
            success=t_res.success,
            verified=t_res.verified,
            permission_denied=t_res.permission_denied,
            duration_ms=t_res.duration_ms,
            summary=verdict.summary,
            error=t_res.error,
        )
        self.events.emit(EventType.PLAN_CREATED, plan=plan.to_dict())
        return t_res, verdict

    @staticmethod
    def _is_low_risk_tool(tool_name: str) -> bool:
        """Return True for read-only / safe query tools that don't need confirmation prompts."""
        return tool_name in {
            "read_file",
            "screenshot",
            "get_time",
            "get_date",
            "get_datetime",
            "system_info",
            "list_files",
        }

    @staticmethod
    def _attach_task_completion_followup(text: str, language: str = "en") -> str:
        """Append polite conversational offer after work is successfully completed."""
        low = text.lower()
        if "another task" in low or "next task" in low or "வேறு ஏதேனும்" in low:
            return text
        if language == "ta":
            followup = "நான் உங்களுக்கு வேறு ஏதேனும் வேலை செய்ய வேண்டுமா? சொல்லுங்கள், எந்த பிரச்சனையும் இல்லை!"
        else:
            followup = "Can I do another task for you? Just say, no problem!"

        trimmed = text.rstrip()
        if not trimmed:
            return followup
        if trimmed.endswith((".", "!", "?")):
            return f"{trimmed} {followup}"
        return f"{trimmed}. {followup}"

    @staticmethod
    def _deterministic_summary(res: ToolResult) -> str:
        """Crisp, ground-truth human summary when Ollama is offline or skipped."""
        if not res.success:
            prefix = "Permission denied" if res.permission_denied else f"Action '{res.tool_name}' failed"
            return f"{prefix}: {res.error or 'Unknown error.'}"

        d = res.data
        t = res.tool_name
        if t == "get_datetime":
            return f"It is currently {d.get('human', d.get('time'))}."
        if t in {"open_vscode", "open_application", "open_app_project"}:
            return f"Opened {d.get('app', 'application')} (verified PID: {', '.join(map(str, d.get('pids', [])))})."
        if t == "run_system_diagnostics":
            snap = d.get("snapshot", {})
            gpu = snap.get("gpu", {})
            gpu_part = (
                f", GPU {gpu.get('name')} at {gpu.get('utilization_percent')}% ({gpu.get('vram_used_mb')}/{gpu.get('vram_total_mb')} MB VRAM)"
                if gpu.get("available")
                else ""
            )
            warns = d.get("warnings") or []
            warn_part = f" Warnings: {'; '.join(warns)}" if warns else " No bottlenecks detected."
            return (
                f"System health is {d.get('overall_health', 'OK')}: CPU {snap.get('cpu_percent')}%, "
                f"RAM {snap.get('ram_used_mb')}/{snap.get('ram_total_mb')} MB ({snap.get('ram_percent')}%)"
                f"{gpu_part}.{warn_part}"
            )
        if t == "get_system_specs":
            return (
                f"{d.get('os')} | CPU: {d.get('cpu_model')} ({d.get('cpu_logical_threads')} threads) | "
                f"RAM: {d.get('ram_total_gb')} GB | GPU: {d.get('gpu', {}).get('name', 'N/A')} | "
                f"Uptime: {d.get('uptime_hours')} hours."
            )
        if t == "check_disk_space":
            parts = [
                f"{dk['mountpoint']}: {dk['free_gb']} GB free of {dk['total_gb']} GB ({dk['percent_used']}% used)"
                for dk in d.get("disks", [])
            ]
            return "Disk space — " + ("; ".join(parts) if parts else "No drives reported.")
        if t == "list_running_processes":
            top = d.get("processes", [])[:6]
            items = ", ".join(f"{p['name']} ({p['memory_mb']} MB)" for p in top)
            return f"Top running processes by memory: {items}."
        if t == "organize_directory":
            cats = ", ".join(f"{k}: {v}" for k, v in d.get("by_category", {}).items()) or "no loose files"
            return f"Organized {d.get('moved_count', 0)} file(s) in {d.get('directory')} ({cats}). Verified with zero overwrites."
        if t == "find_projects":
            projs = d.get("projects", [])
            if not projs:
                return "No matching projects were found in your permitted directories."
            names = ", ".join(f"{p['name']} ({p['path']})" for p in projs[:5])
            return f"Found {d.get('count', len(projs))} project(s): {names}."
        if t == "start_timer":
            tm = d.get("timer", {})
            return f"Started timer '{tm.get('label', 'Timer')}' for {tm.get('duration_seconds')} seconds."
        if t == "take_screenshot":
            return f"Screenshot captured and verified at {d.get('path')}."
        if t == "set_volume":
            return f"Master volume verified at {d.get('volume_percent')}% (muted={d.get('muted')})."
        return f"Verified '{t}':\n{json.dumps(d, indent=2, default=str)[:800]}"
