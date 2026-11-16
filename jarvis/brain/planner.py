"""Task Plan data structures and initial plan builder (Section 6 & 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from jarvis.brain.intent import IntentAnalysis, IntentKind


@dataclass
class PlanStep:
    index: int
    description: str
    status: str = "pending"   # pending | running | verified | failed | denied | skipped
    tool_name: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "description": self.description,
            "status": self.status,
            "tool_name": self.tool_name,
            "detail": self.detail,
        }


@dataclass
class ExecutionPlan:
    goal: str
    intent: IntentKind
    steps: List[PlanStep] = field(default_factory=list)

    def update_from_llm_plan(self, descriptions: List[str]) -> None:
        if not descriptions:
            return
        completed = [s for s in self.steps if s.status in {"verified", "failed", "denied"}]
        start_idx = len(completed) + 1
        remaining = [
            PlanStep(index=start_idx + i, description=desc)
            for i, desc in enumerate(descriptions[:8])
        ]
        self.steps = completed + remaining

    def record_step(self, tool_name: str, description: str, status: str, detail: str = "") -> PlanStep:
        # Update first pending step if available, otherwise append
        for step in self.steps:
            if step.status in {"pending", "running"}:
                step.tool_name = tool_name
                step.description = description or step.description
                step.status = status
                step.detail = detail
                return step
        new_step = PlanStep(
            index=len(self.steps) + 1,
            description=description or f"Execute {tool_name}",
            status=status,
            tool_name=tool_name,
            detail=detail,
        )
        self.steps.append(new_step)
        return new_step

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "intent": self.intent.value,
            "steps": [s.to_dict() for s in self.steps],
        }


class TaskPlanner:
    """Creates initial execution plans refined dynamically during the agent loop."""

    def create_initial_plan(self, goal: str, analysis: IntentAnalysis) -> ExecutionPlan:
        if not analysis.requires_tools:
            return ExecutionPlan(
                goal=goal,
                intent=analysis.kind,
                steps=[PlanStep(1, "Generate direct conversational response")],
            )
        if analysis.fast_tool:
            return ExecutionPlan(
                goal=goal,
                intent=analysis.kind,
                steps=[
                    PlanStep(1, f"Execute & verify '{analysis.fast_tool}'", tool_name=analysis.fast_tool),
                    PlanStep(2, "Report verified outcome"),
                ],
            )
        return ExecutionPlan(
            goal=goal,
            intent=analysis.kind,
            steps=[PlanStep(1, analysis.summary or "Analyze goal and select first verified tool")],
        )
