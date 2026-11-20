"""corner_preview (improvement tasks only): the evaluator's own worst-case sweep exposed to the agent as an ADVISORY check. Stubbed checks_fn; real-EDA parity test is in tests/integration."""
from __future__ import annotations

from pathlib import Path

import pytest

from ae.harness.contracts import ToolError
from ae.harness.tools import AgentContext, build_registry
from ae.harness.workspace import Workspace

from tests.unit.test_improvement import PRISTINE


def make_ctx(tmp_path, improvement: bool, max_previews: int = 2):
    ws = Workspace(tmp_path / "ws")
    ws.init(PRISTINE)
    calls = []

    def checks_fn(names, manifest, workdir):
        calls.append(list(names))
        return {n: {"outcome": "passed", "measurements": {"alias_corners_worst_dB": -58.7}} for n in names}
    ctx = AgentContext(ws, tmp_path / "scratch", failure_report={}, checks_fn=checks_fn, improvement=improvement, max_corner_previews=max_previews, trusted_combined="x" * 64)
    return ctx, calls


def test_corner_preview_is_refused_outside_improvement_tasks(tmp_path):
    ctx, calls = make_ctx(tmp_path, improvement=False)
    res = build_registry(ctx).call("run_check", {"check": "corner_preview"})
    assert not res.ok and res.error["code"] == "invalid_arguments" and calls == []


def test_corner_preview_runs_in_improvement_tasks_is_cached_per_design_and_budgeted(tmp_path):
    ctx, calls = make_ctx(tmp_path, improvement=True, max_previews=2)
    reg = build_registry(ctx)
    first = reg.call("run_check", {"check": "corner_preview"})
    again = reg.call("run_check", {"check": "corner_preview"})
    assert first.ok and again.ok and again.result["cache_hit"] is True and calls == [["corner_preview"]] and ctx.corner_previews == 1      # same design: no second sweep, no budget spent
    ws = ctx.workspace
    ws.propose({"summary": "s", "base_sha256": ws.sha256(), "ops": [{"op": "set_field", "part": "C3", "field": "value", "to": "5.1n"}, {"op": "set_field", "part": "C3", "field": "mpn_candidate", "to": "C0805C512J5GACTU"}]})
    assert reg.call("run_check", {"check": "corner_preview"}).ok and ctx.corner_previews == 2
    ws.propose({"summary": "s", "base_sha256": ws.sha256(), "ops": [{"op": "set_field", "part": "C3", "field": "value", "to": "6.2n"}, {"op": "set_field", "part": "C3", "field": "mpn_candidate", "to": "C0805C622J5GACTU"}]})
    third = reg.call("run_check", {"check": "corner_preview"})
    assert not third.ok and third.error["code"] == "preview_budget_exhausted" and len(calls) == 2


def test_tool_description_and_prompt_say_it_is_advisory():
    from ae.harness.controller import IMPROVEMENT_PROMPT
    assert "advisory" in IMPROVEMENT_PROMPT and "corner_preview" in IMPROVEMENT_PROMPT and "evaluator still decides" in IMPROVEMENT_PROMPT


def test_blocked_and_error_previews_are_not_cached_and_errors_do_not_spend_budget(tmp_path):
    ctx, calls = make_ctx(tmp_path, improvement=True, max_previews=2)
    outcomes = iter(["error", "blocked", "within_contract_limits"])
    ctx.checks_fn = lambda names, manifest, workdir: (calls.append(1) or {n: {"outcome": next(outcomes)} for n in names})
    reg = build_registry(ctx)
    assert reg.call("run_check", {"check": "corner_preview"}).result["outcome"] == "error" and ctx.corner_previews == 0       # netlist-style failure: free, retryable
    assert reg.call("run_check", {"check": "corner_preview"}).result["outcome"] == "blocked" and ctx.corner_previews == 1      # a real sweep that did not finish costs budget but is not cached
    third = reg.call("run_check", {"check": "corner_preview"})
    assert third.result["outcome"] == "within_contract_limits" and third.result["cache_hit"] is False and len(calls) == 3


def test_preview_gain_limits_agree_with_the_contract():
    from ae.harness.checks import _limits
    import ae.mc as mc, inspect
    src = inspect.getsource(mc.run_corners)
    lo, hi = _limits()["gain"]
    assert f"{lo} <=" in src and f"<= {hi}" in src                                       # run_corners hardcodes the same 1.235..1.365 window the preview reports
