"""Brain robustness: malformed LLM output, unknown tools and invalid arguments.

Small local models (3B–8B) produce truncated JSON, markdown fences, XML tool
tags, invented tool names and wrong argument types. None of that may crash the
agent or reach a tool handler.
"""

from __future__ import annotations

import pytest

from jarvis.brain.output_parser import StructuredOutputParser
from jarvis.security.policy import RiskLevel
from jarvis.tools.base import ToolParam, ToolSpec

VALID_TOOLS = {"open_vscode", "create_file", "delete_path", "get_datetime"}


@pytest.fixture(scope="module")
def parser() -> StructuredOutputParser:
    return StructuredOutputParser()


# ---------------------------------------------------------------------------
# 1. Malformed / truncated JSON never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"action": "open_vscode"',                       # missing closing brace
        '{"action": "create_file", "args": {"path": "a.t',  # truncated mid-string
        '{"action": "create_file", "args": {"path": "a.txt",}',  # trailing comma, unterminated
        '{"action": }',
        '{"action": "open_vscode" "args": {}}',            # missing comma
        "",                                                 # empty output
        "   \n  ",                                          # whitespace only
        '{"action": "open_vscode", "args": {"path": "x"}} trailing junk {{{',  # recoverable
    ],
)
def test_malformed_or_truncated_json_never_raises(parser: StructuredOutputParser, raw: str):
    step = parser.parse(raw, valid_tools=VALID_TOOLS)
    assert step.kind in {"tool_call", "final_answer", "parse_error"}
    if step.kind == "parse_error":
        assert step.error  # always explains itself, never silently "succeeds"


def test_truncated_json_is_reported_as_parse_error(parser: StructuredOutputParser):
    step = parser.parse('{"action": "create_file", "args": {"path": "notes.tx', valid_tools=VALID_TOOLS)
    assert step.kind == "parse_error"
    assert "valid structured JSON" in step.error


def test_plain_prose_returns_parse_error_with_answer_preserved(parser: StructuredOutputParser):
    step = parser.parse("Sure! I will open VS Code for you right away.", valid_tools=VALID_TOOLS)
    assert step.kind == "parse_error"
    assert "VS Code" in step.answer


def test_fenced_json_and_trailing_commas_are_repaired(parser: StructuredOutputParser):
    fenced = '```json\n{"action": "open_vscode", "args": {},}\n```'
    step = parser.parse(fenced, valid_tools=VALID_TOOLS)
    assert step.kind == "tool_call"
    assert step.tool_name == "open_vscode"


def test_xml_tool_call_and_think_trace_are_understood(parser: StructuredOutputParser):
    raw = (
        "<think>The user wants the time.</think>\n"
        '<tool_call>{"action": "get_datetime", "args": {}}</tool_call>'
    )
    step = parser.parse(raw, valid_tools=VALID_TOOLS)
    assert step.kind == "tool_call"
    assert step.tool_name == "get_datetime"
    assert "time" in step.thought.lower()
    assert "<think>" not in step.thought


def test_stringified_args_are_parsed(parser: StructuredOutputParser):
    step = parser.parse(
        '{"action": "create_file", "args": "{\\"path\\": \\"a.txt\\", \\"content\\": \\"hi\\"}"}',
        valid_tools=VALID_TOOLS,
    )
    assert step.kind == "tool_call"
    assert step.tool_args == {"path": "a.txt", "content": "hi"}


# ---------------------------------------------------------------------------
# 2. Unknown tool names and missing actions are rejected
# ---------------------------------------------------------------------------


def test_unknown_tool_name_is_rejected(parser: StructuredOutputParser):
    step = parser.parse('{"action": "format_hard_drive", "args": {}}', valid_tools=VALID_TOOLS)
    assert step.kind == "parse_error"
    assert "Unknown tool" in step.error
    assert "format_hard_drive" in step.error


def test_json_without_action_is_a_parse_error(parser: StructuredOutputParser):
    step = parser.parse('{"thought": "hmm", "args": {}}', valid_tools=VALID_TOOLS)
    assert step.kind == "parse_error"
    assert "missing 'action'" in step.error


def test_tool_call_wrapper_action_is_resolved(parser: StructuredOutputParser):
    step = parser.parse(
        '{"action": "tool_call", "tool_name": "get_datetime", "args": {}}', valid_tools=VALID_TOOLS
    )
    assert step.kind == "tool_call"
    assert step.tool_name == "get_datetime"


def test_final_answer_kinds(parser: StructuredOutputParser):
    for action in ("final_answer", "done", "answer", "none"):
        step = parser.parse(f'{{"action": "{action}", "answer": "All done."}}', valid_tools=VALID_TOOLS)
        assert step.kind == "final_answer", action
        assert step.answer == "All done."


def test_unverified_action_claim_is_detected(parser: StructuredOutputParser):
    assert parser.looks_like_unverified_action_claim("I have deleted your Downloads folder.") is True
    assert parser.looks_like_unverified_action_claim("Here is how a for loop works.") is False


# ---------------------------------------------------------------------------
# 3. Registry schema validation rejects bad arguments
# ---------------------------------------------------------------------------


def _make_spec(calls: list) -> ToolSpec:
    def handler(args, _token):
        calls.append(args)
        return {"success": True, "verified": True}

    return ToolSpec(
        name="demo_tool",
        category="utilities",
        description="Demo tool for schema validation tests.",
        parameters=[
            ToolParam("title", "string", "Required title"),
            ToolParam("count", "integer", "Optional count", required=False, default=3),
            ToolParam("priority", "string", "Priority", required=False, default="medium", enum=["low", "medium", "high"]),
        ],
        risk_level=RiskLevel.SAFE,
        handler=handler,
    )


def test_valid_arguments_are_coerced_and_defaults_applied():
    calls: list = []
    spec = _make_spec(calls)
    args, err = spec.validate_and_coerce({"title": "hello", "count": "5"})
    assert err is None
    assert args == {"title": "hello", "count": 5, "priority": "medium"}


@pytest.mark.parametrize(
    "raw_args, expected",
    [
        ({}, "Missing required parameter 'title'"),
        ({"title": "x", "priority": "urgent"}, "must be one of"),
        ({"title": "x", "count": "many"}, "Invalid parameter 'count'"),
        ({"title": "x", "count": True}, "expected integer, got boolean"),
        ([1, 2, 3], "must be a JSON object"),
    ],
)
def test_invalid_arguments_are_rejected_without_calling_handler(raw_args, expected):
    calls: list = []
    spec = _make_spec(calls)
    args, err = spec.validate_and_coerce(raw_args)
    assert args == {}
    assert err is not None and expected in err
    assert calls == [], "handler must never run on invalid arguments"


def test_registry_rejects_unknown_tool_without_executing(controller):
    result = controller.tools.execute("format_hard_drive", {})
    assert result.success is False
    assert "Unknown tool" in (result.error or "")
    # A rejection is not an execution attempt and must not be audited as an outcome.
    assert not any(e.get("action") == "format_hard_drive" for e in controller.audit.read_recent(limit=20))


def test_registry_rejects_invalid_enum_before_security_gate(controller):
    before = len(controller.audit.read_recent(limit=200))
    result = controller.tools.execute("add_todo", {"title": "call mum", "priority": "urgent"})
    assert result.success is False
    assert "must be one of" in (result.error or "")
    after = len(controller.audit.read_recent(limit=200))
    assert after == before, "schema-invalid calls must not reach the SecurityGate"


def test_registry_rejects_missing_required_argument(controller):
    result = controller.tools.execute("create_folder", {})
    assert result.success is False
    assert "Missing required parameter" in (result.error or "")


def test_allowlisted_read_only_tool_runs_end_to_end(controller):
    result = controller.tools.execute("get_datetime", {})
    assert result.success is True
    assert result.verified is True
