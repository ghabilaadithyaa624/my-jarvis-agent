"""Independent Result Verifier & Error Recovery Advisor (Section 6 & 7).

Prevents JARVIS from ever claiming success when a tool failed or was unverified,
and provides concrete recovery suggestions for the execution loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

from jarvis.tools.base import ToolResult


class VerdictStatus(str, Enum):
    VERIFIED_SUCCESS = "verified_success"
    UNVERIFIED_PARTIAL = "unverified_partial"
    PERMISSION_DENIED = "permission_denied"
    RECOVERABLE_FAILURE = "recoverable_failure"
    FATAL_FAILURE = "fatal_failure"


@dataclass
class VerificationReport:
    status: VerdictStatus
    verified: bool
    summary: str
    recovery_hint: str = ""


class ResultVerifier:
    """Evaluates ToolResult ground truth and guides bounded error recovery."""

    def evaluate(self, result: ToolResult) -> VerificationReport:
        if result.permission_denied:
            return VerificationReport(
                status=VerdictStatus.PERMISSION_DENIED,
                verified=True,
                summary=f"Permission denied for '{result.tool_name}': {result.error}",
                recovery_hint=(
                    "Do NOT retry the denied operation. Explain clearly to the user why it was blocked "
                    "or declined, and let them know how to allow it in Permissions if desired."
                ),
            )

        if result.success and result.verified:
            # Check empty search results so agent knows to try alternate query if needed
            if result.tool_name in {"search_files", "find_projects"}:
                count = int(result.data.get("count", -1))
                if count == 0:
                    return VerificationReport(
                        status=VerdictStatus.VERIFIED_SUCCESS,
                        verified=True,
                        summary=f"'{result.tool_name}' completed and verified 0 matching items.",
                        recovery_hint=(
                            "0 items matched. Consider searching with a shorter keyword, an extension filter "
                            "(e.g. extension='.blend'), or checking recall_facts for a saved project path."
                        ),
                    )
            return VerificationReport(
                status=VerdictStatus.VERIFIED_SUCCESS,
                verified=True,
                summary=f"'{result.tool_name}' succeeded and passed verification ({result.duration_ms} ms).",
            )

        if result.success and not result.verified:
            return VerificationReport(
                status=VerdictStatus.UNVERIFIED_PARTIAL,
                verified=False,
                summary=f"'{result.tool_name}' ran, but outcome could not be independently verified.",
                recovery_hint="Verify outcome with an inspection tool (e.g. is_application_running or list_directory) or explicitly inform the user verification was partial.",
            )

        # Tool failed — determine whether recovery makes sense
        err = (result.error or "Unknown failure").strip()
        err_low = err.lower()

        if any(k in err_low for k in ("requires windows 11", "not installed", "refused to overwrite")):
            return VerificationReport(
                status=VerdictStatus.FATAL_FAILURE,
                verified=result.verified,
                summary=f"'{result.tool_name}' could not run: {err}",
                recovery_hint="Report this exact prerequisite or safeguard honestly to the user.",
            )

        if result.tool_name == "run_python_script" and result.data.get("traceback_summary"):
            return VerificationReport(
                status=VerdictStatus.RECOVERABLE_FAILURE,
                verified=True,
                summary=f"Python script exited with error: {err}",
                recovery_hint="Inspect traceback_summary; inform the user of the exact line/exception or fix a script you just created.",
            )

        return VerificationReport(
            status=VerdictStatus.RECOVERABLE_FAILURE,
            verified=result.verified,
            summary=f"'{result.tool_name}' failed: {err}",
            recovery_hint="Check parameter names/paths, try searching for the correct path first, or report the failure accurately.",
        )

    @staticmethod
    def aggregate_task_verification(results: List[ToolResult]) -> tuple[bool, str]:
        """Compute truthful overall task verification status across all executed steps."""
        if not results:
            return False, "No tools were executed."
        all_ok = all(r.success for r in results)
        all_ver = all(r.verified for r in results)
        any_ok = any(r.success and r.verified for r in results)
        if all_ok and all_ver:
            return True, f"All {len(results)} step(s) succeeded and were verified."
        if any_ok:
            ok_cnt = sum(1 for r in results if r.success and r.verified)
            return (
                results[-1].success and results[-1].verified,
                f"{ok_cnt}/{len(results)} step(s) succeeded and verified (final step {'verified' if (results[-1].success and results[-1].verified) else 'failed/unverified'}).",
            )
        return False, f"0/{len(results)} step(s) succeeded."
