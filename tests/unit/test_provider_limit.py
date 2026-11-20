"""Provider usage-limit handling (backlog item 1 of docs/agent-improvement-mode.md section 16). Offline: no model, no docker, no sleeping.
Real failure strings are copied from improve-live-7 / improve-live-8 trace.jsonl (model_reply events; the separator is U+00B7)."""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ae.harness.controller import Controller, Limits, Mode, preview_spend
from ae.harness.evaluator import TrustedEvaluator
from ae.harness.providers import ModelReply, ScriptedProvider, parse_usage_limit, usage_limit_wait_seconds, wait_for
from ae.harness.tasks import load_task
from ae.harness.workspace import REPO_ROOT

from tests.unit.test_improvement import A
from tests.unit.test_improvement_feedback_loop import NOOP, NOOP2, NOOP3, TASK_ID, Rig

LONDON = ZoneInfo("Europe/London")
TEXT_8 = "You've hit your 5-hour limit · resets 2:10pm (Europe/London)"
ERROR_8 = "provider_error: You've hit your 5-hour limit · resets 2:10pm (Europe/London) (api_error_status=429)"
TEXT_7 = "You've hit your 5-hour limit · resets 9:10am (Europe/London)"
ERROR_7 = "provider_error: You've hit your 5-hour limit · resets 9:10am (Europe/London) (api_error_status=429)"
# the two real model_reply trace lines, verbatim (live-7 then live-8)
TRACE_LINE_7 = '{"seq": 90, "t": 600.0, "type": "model_reply", "text": "You\'ve hit your 5-hour limit \\u00b7 resets 9:10am (Europe/London)", "error": "provider_error: You\'ve hit your 5-hour limit \\u00b7 resets 9:10am (Europe/London) (api_error_status=429)", "usage": null, "cost_usd": null, "cost_basis": null, "model": null, "elapsed_s": 1.2}'
TRACE_LINE_8 = '{"seq": 134, "t": 973.41, "type": "model_reply", "text": "You\'ve hit your 5-hour limit \\u00b7 resets 2:10pm (Europe/London)", "error": "provider_error: You\'ve hit your 5-hour limit \\u00b7 resets 2:10pm (Europe/London) (api_error_status=429)", "usage": null, "cost_usd": null, "cost_basis": null, "model": null, "elapsed_s": 1.31}'


# ---------------------------------------------------------------- (a) parser
def test_parse_real_live8_string_next_occurrence_same_day_and_across_midnight():
    before = datetime(2026, 11, 20, 13, 0, tzinfo=LONDON)
    info = parse_usage_limit(TEXT_8, 429, now=before)
    assert info["kind"] == "5-hour limit" and info["api_error_status"] == 429 and info["message"] == TEXT_8
    assert info["reset_text"] == "2:10pm (Europe/London)"
    assert datetime.fromisoformat(info["reset_at_iso"]) == datetime(2026, 11, 20, 14, 10, tzinfo=LONDON)
    after = datetime(2026, 11, 20, 15, 0, tzinfo=LONDON)                           # 2:10pm already passed today -> tomorrow
    assert datetime.fromisoformat(parse_usage_limit(TEXT_8, 429, now=after)["reset_at_iso"]) == datetime(2026, 11, 21, 14, 10, tzinfo=LONDON)
    exactly = datetime(2026, 11, 20, 14, 10, tzinfo=LONDON)                         # strictly after now
    assert datetime.fromisoformat(parse_usage_limit(TEXT_8, 429, now=exactly)["reset_at_iso"]) == datetime(2026, 11, 21, 14, 10, tzinfo=LONDON)


def test_parse_real_live7_string_9_10am_rolls_to_next_day_when_already_past():
    now = datetime(2026, 11, 20, 10, 0, tzinfo=LONDON)
    info = parse_usage_limit(TEXT_7, 429, now=now)
    assert info["reset_text"] == "9:10am (Europe/London)"
    assert datetime.fromisoformat(info["reset_at_iso"]) == datetime(2026, 11, 21, 9, 10, tzinfo=LONDON)
    assert datetime.fromisoformat(parse_usage_limit(TEXT_7, 429, now=datetime(2026, 11, 20, 8, 0, tzinfo=LONDON))["reset_at_iso"]) == datetime(2026, 11, 20, 9, 10, tzinfo=LONDON)


def test_parse_uses_the_stated_zone_not_the_zone_of_now():
    now = datetime(2026, 11, 20, 23, 30, tzinfo=timezone.utc)                       # 23:30 UTC = 23:30 London (winter); 2:10pm London is tomorrow
    assert datetime.fromisoformat(parse_usage_limit(TEXT_8, 429, now=now)["reset_at_iso"]) == datetime(2026, 11, 21, 14, 10, tzinfo=LONDON)
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)                       # BST: 2:10pm London == 13:10 UTC
    assert parse_usage_limit(TEXT_8, 429, now=summer)["reset_at_iso"].endswith("+01:00")


@pytest.mark.parametrize("clock,expected", [("9am", (9, 0)), ("12am", (0, 0)), ("12pm", (12, 0)), ("12:30pm", (12, 30)), ("14:10", (14, 10)), ("9:05 AM", (9, 5))])
def test_parse_clock_formats(clock, expected):
    now = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)
    iso = parse_usage_limit(f"You've hit your 5-hour limit · resets {clock} (UTC)", 429, now=now)["reset_at_iso"]
    when = datetime.fromisoformat(iso)
    assert (when.hour, when.minute) == expected and when > now


def test_parse_without_zone_uses_the_system_local_zone():
    now = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)
    when = datetime.fromisoformat(parse_usage_limit("You've hit your 5-hour limit · resets 3pm", 429, now=now)["reset_at_iso"])
    assert when.tzinfo is not None
    assert (when.hour, when.minute) == (15, 0) and when > now


def test_parse_non_limit_and_unparseable_cases():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert parse_usage_limit("internal server error", 500, now=now) is None
    assert parse_usage_limit("", None, now=now) is None
    assert parse_usage_limit("garbage \x00 {not json", None, now=now) is None
    unparseable = parse_usage_limit("rate limited, try later", 429, now=now)           # 429 alone is enough; no reset information
    assert unparseable is not None and unparseable["kind"] == "unknown" and unparseable["reset_at_iso"] is None and unparseable["reset_text"] is None
    for text in ("You've hit your 5-hour limit · resets Nov 21, 2pm", "You've hit your limit · resets 25:99 (UTC)", "You've hit your limit · resets 14", "You've hit your limit · resets 2pm (Not/AZone)", "You've hit your limit · resets 13pm (UTC)"):
        got = parse_usage_limit(text, None, now=now)
        assert got is not None and got["reset_at_iso"] is None, text


# ---------------------------------------------------------------- provider plumbing
def test_scripted_provider_returns_a_model_reply_item_unchanged():
    reply = ModelReply("x", error="e", usage_limit={"k": 1})
    assert ScriptedProvider([reply]).complete("s", "t", 1.0) is reply


def test_claude_cli_provider_marks_usage_limit_errors(monkeypatch):
    import subprocess

    from ae.harness.providers import ClaudeCliProvider
    out = json.dumps({"is_error": True, "result": TEXT_8, "api_error_status": 429, "subtype": "success"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=out, stderr=""))
    reply = ClaudeCliProvider("m").complete("sys", "hi", 5)
    assert reply.error == ERROR_8.replace("provider_error", "provider_usage_limit") and reply.usage_limit["kind"] == "5-hour limit" and reply.usage_limit["api_error_status"] == 429
    other = json.dumps({"is_error": True, "result": "boom", "api_error_status": 500})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=other, stderr=""))
    reply = ClaudeCliProvider("m").complete("sys", "hi", 5)
    assert reply.error.startswith("provider_error: boom") and reply.usage_limit is None


# ---------------------------------------------------------------- (b) controller
def limit_reply() -> ModelReply:
    return ModelReply(TEXT_8, elapsed_s=1.31, error=ERROR_8.replace("provider_error", "provider_usage_limit"), usage_limit=parse_usage_limit(TEXT_8, 429))


def test_usage_limit_stops_without_charging_the_call_and_is_recorded(tmp_path):
    rig = Rig(tmp_path)
    ctl = rig.controller([A("get_design"), limit_reply()], Limits(max_iterations=3, max_model_calls=10))
    s = ctl.run()
    assert s["stop_reason"] == "provider_usage_limit" and s["model_calls"] == 1                       # only get_design was charged
    state = json.loads((tmp_path / "run/state.json").read_text())
    assert state["usage_limit"]["kind"] == "5-hour limit" and state["usage_limit_hits"] == 1 and state["usage"]["cost_unknown_calls"] == 0     # the refused call is not a cost-unknown call
    assert state["usage"]["model_seconds"] >= 1.31
    events = rig.events()
    hit = [e for e in events if e["type"] == "provider_usage_limit"]
    assert len(hit) == 1 and hit[0]["reset_text"] == "2:10pm (Europe/London)"
    stop = [e for e in events if e["type"] == "transition" and e["to"] == "STOPPED"][-1]
    assert stop["reason"] == "provider_usage_limit" and stop["reset_text"] == "2:10pm (Europe/London)" and stop["reset_at_iso"]


def test_plain_provider_errors_keep_their_stop_reason(tmp_path):
    rig = Rig(tmp_path)
    s = rig.controller([A("get_design"), ModelReply("", error="provider_error: boom (api_error_status=500)")], Limits(max_iterations=3, max_model_calls=10)).run()
    assert s["stop_reason"] == "provider_error" and s["model_calls"] == 2


def resume(rig: Rig, script, limits=Limits(max_iterations=3, max_model_calls=20)):
    ev = TrustedEvaluator()
    ev.run_pipeline = rig._pipe
    return Controller.resume(rig.root / "run", task=load_task(TASK_ID), provider=ScriptedProvider(script), evaluator=ev, limits=limits, mode=Mode.ENGINEERED_SINGLE,
                             baseline_record=rig.base, baseline_dir=rig.base_dir, checks_fn=lambda names, p, w: {n: {"outcome": "passed"} for n in names})


def test_resume_after_usage_limit_keeps_history_clears_the_stop_and_marks_the_trace(tmp_path):
    rig = Rig(tmp_path)
    rig.controller([A("get_design"), limit_reply()], Limits(max_iterations=3, max_model_calls=10)).run()
    ctl = resume(rig, [A("get_design"), A("declare_no_improvement", summary="done")])
    assert ctl.state["stop_reason"] is None and ctl.state["usage_limit"]["kind"] == "5-hour limit"
    assert ctl.continue_run()["stop_reason"] == "no_improvement_declared"
    resumed = [e for e in rig.events() if e["type"] == "resumed"]
    assert resumed[-1]["resumed_after"] == "provider_usage_limit"
    assert not any(m["role"] == "assistant" and "hit your" in m["content"] for m in ctl.messages)       # the failed reply never enters the transcript


# ---------------------------------------------------------------- (c) preview budget survives a resume
def set_cap(rig: Rig, cap: int):
    def first_turn(transcript: str) -> str:
        rig.ctl.ctx.max_corner_previews = cap
        return json.dumps(A("get_design"))
    return first_turn


def preview_results(rig: Rig) -> list[dict]:
    return [e["result"] for e in rig.events() if e["type"] == "tool_call" and e["tool"] == "run_check"]


def three_previews_then_limit(rig: Rig) -> Controller:
    ctl = rig.controller([set_cap(rig, 4), A("run_check", check="corner_preview"), NOOP, A("run_check", check="requirements_preview"), NOOP2, A("run_check", check="corner_preview"), limit_reply()],
                         Limits(max_iterations=3, max_model_calls=20))
    ctl.run()
    return ctl


def test_preview_budget_survives_resume(tmp_path):
    rig = Rig(tmp_path)
    ctl = three_previews_then_limit(rig)
    assert ctl.state["stop_reason"] == "provider_usage_limit" and ctl.state["corner_previews_used"] == 3 == ctl.ctx.corner_previews
    resumed = resume(rig, [A("get_design"), A("run_check", check="corner_preview"), NOOP3, A("run_check", check="corner_preview")])
    assert resumed.ctx.corner_previews == 3                                                          # not reset to 0 by the resume
    resumed.ctx.max_corner_previews = 4
    resumed.continue_run()
    last = preview_results(rig)[-2:]
    assert last[0]["ok"] is True and last[0]["result"]["cache_hit"] is False                          # the 4th fresh preview is the last one allowed
    assert last[1]["ok"] is False and last[1]["error"]["code"] == "preview_budget_exhausted"
    assert resumed.state["corner_previews_used"] == 4


def test_old_state_without_the_key_is_reconstructed_from_the_trace(tmp_path):
    rig = Rig(tmp_path)
    three_previews_then_limit(rig)
    state_path = tmp_path / "run/state.json"
    state = json.loads(state_path.read_text())
    del state["corner_previews_used"]
    state_path.write_text(json.dumps(state))
    assert resume(rig, [A("get_design")]).ctx.corner_previews == 3
    state["corner_previews_used"] = 1                                                                 # a key that is present wins over the trace
    state_path.write_text(json.dumps(state))
    assert resume(rig, [A("get_design")]).ctx.corner_previews == 1


def test_preview_spend_rules():
    def event(check="corner_preview", ok=True, outcome="passed", cache_hit=False, code=None):
        return {"type": "tool_call", "tool": "run_check", "args": {"check": check}, "result": {"ok": ok, "result": {"outcome": outcome, "cache_hit": cache_hit} if ok else None, "error": {"code": code} if code else None}}
    assert preview_spend(event()) == 1 and preview_spend(event("requirements_preview", outcome="info")) == 1
    assert preview_spend(event(outcome="blocked")) == 1                                              # a blocked sweep spent the budget (tools.py)
    assert preview_spend(event(outcome="error")) == 0 and preview_spend(event(cache_hit=True)) == 0
    assert preview_spend(event(ok=False, code="preview_budget_exhausted")) == 0 and preview_spend(event("ac_response")) == 0


# ---------------------------------------------------------------- (d) waiting and the script
NOW = datetime(2026, 11, 20, 13, 0, tzinfo=timezone.utc)                                             # = 13:00 London in winter


def old_style_run(run_dir, trace_line: str, written_at: datetime):
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"stop_reason": "provider_error", "model_calls": 34, "iteration": 2}))
    trace = run_dir / "trace.jsonl"
    trace.write_text('{"seq": 1, "t": 0.1, "type": "message", "role": "user", "content": "x"}\n' + trace_line + "\n")
    os.utime(trace, (written_at.timestamp(), written_at.timestamp()))


def test_wait_seconds_from_state_json(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"usage_limit": {"reset_at_iso": (NOW + timedelta(seconds=1000)).isoformat(), "reset_text": "x"}}))
    seconds, info = usage_limit_wait_seconds(tmp_path, now=NOW, margin_s=120)
    assert seconds == pytest.approx(1120.0) and info["reset_text"] == "x"
    assert usage_limit_wait_seconds(tmp_path, now=NOW + timedelta(hours=2), margin_s=120)[0] == 0.0     # reset + margin already past: no wait


def test_wait_seconds_from_an_old_style_trace_only_run_live8_and_live7(tmp_path):
    old_style_run(tmp_path / "l8", TRACE_LINE_8, datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc))      # failure written 12:00Z -> reset 14:10Z
    seconds, info = usage_limit_wait_seconds(tmp_path / "l8", now=NOW, margin_s=120)
    assert info["api_error_status"] == 429 and info["reset_text"] == "2:10pm (Europe/London)" and seconds == pytest.approx(70 * 60 + 120)
    old_style_run(tmp_path / "l7", TRACE_LINE_7, datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc))        # written 08:00Z -> reset 09:10Z, already passed at NOW
    assert usage_limit_wait_seconds(tmp_path / "l7", now=NOW, margin_s=120)[0] == 0.0


def test_wait_seconds_unknown_when_nothing_is_recorded(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"stop_reason": "max_iterations"}))
    assert usage_limit_wait_seconds(tmp_path, now=NOW) == (None, None)


def test_wait_for_sleeps_in_chunks_without_real_sleeping():
    slept, logs = [], []
    wait_for(1500, sleep=slept.append, log=logs.append, chunk_s=600)
    assert slept == [600, 600, 300] and len(logs) == 3 and "25.0 min" in logs[0]


def load_script():
    spec = importlib.util.spec_from_file_location("resume_improvement_run", REPO_ROOT / "scripts/resume_improvement_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_dry_run_prints_the_plan_and_calls_nothing(tmp_path, capsys, monkeypatch):
    script = load_script()
    runs = tmp_path / "runs"
    old_style_run(runs / "r8", TRACE_LINE_8, datetime.now(timezone.utc))
    monkeypatch.setattr("ae.harness.providers.ClaudeCliProvider", lambda *a, **k: pytest.fail("provider constructed in --dry-run"))
    code = script.main(["r8", "improve-wide-retune-alias-v3", "claude-sonnet-5-5", "5", "100", "--runs-dir", str(runs), "--dry-run", "--wait-until-reset"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["stop_reason"] == "provider_error" and out["usage_limit"]["reset_text"] == "2:10pm (Europe/London)"
    assert out["corner_previews_used"] == 0 and out["seconds_to_wait"] is not None and out["reset_at_iso"]


def test_script_refuses_to_wait_without_a_reset_time_or_for_too_long(tmp_path, capsys):
    script = load_script()
    runs = tmp_path / "runs"
    (runs / "none").mkdir(parents=True)
    (runs / "none/state.json").write_text(json.dumps({"stop_reason": "provider_error"}))
    base = ["improve-wide-retune-alias-v3", "m", "5", "100", "--runs-dir", str(runs), "--wait-until-reset"]
    assert script.main(["none"] + base) == 2 and "no usage-limit reset time" in capsys.readouterr().err
    (runs / "far").mkdir()
    far = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()
    (runs / "far/state.json").write_text(json.dumps({"stop_reason": "provider_usage_limit", "usage_limit": {"reset_at_iso": far, "reset_text": "x"}}))
    assert script.main(["far"] + base) == 2 and "refusing to wait" in capsys.readouterr().err


def test_script_has_no_import_time_side_effects():
    module = load_script()
    assert callable(module.main) and callable(module.build_parser)
