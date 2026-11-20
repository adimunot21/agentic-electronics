"""Model providers. A provider turns (system prompt, transcript text) into a reply plus honest usage information.

ClaudeCliProvider: the logged-in `claude` CLI in headless mode with ALL built-in tools disabled, no session persistence, no settings/MCP, run from an empty temp cwd.
  Verified in this environment 2026-11-18 (model claude-sonnet-5-5, auth: claude.ai). Cost reported by the CLI is a LIST-BASIS ESTIMATE; real billing is unknown (None => unknown, never 0).
ScriptedProvider: FIXTURE for deterministic controller tests. Its runs are labelled `fixture=True` everywhere and never count as agent success.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Real failure text (improve-live-7/-8 trace.jsonl, model_reply events; the separator is U+00B7):
#   "You've hit your 5-hour limit · resets 2:10pm (Europe/London)"   with api_error_status=429 and no other structured reset field.
# The reset is a bare wall-clock time in a named IANA zone, so the absolute instant is "the next occurrence of that time after now".
_LIMIT_PHRASE = re.compile(r"hit your .{0,40}limit", re.IGNORECASE)
_LIMIT_KIND = re.compile(r"hit your (.{0,40}?limit)", re.IGNORECASE)
_RESET_CLOCK = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\))?", re.IGNORECASE)


def _next_wall_clock(hour: int, minute: int, tz_name: str | None, now: datetime) -> datetime | None:
    """First instant strictly after `now` whose wall clock in the zone reads hour:minute (zone default = system local zone)."""
    if tz_name:
        try:
            zone = ZoneInfo(tz_name.strip())
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None                                   # an unknown zone name must not silently become some other zone
        now_zoned = now.astimezone(zone)
    else:
        now_zoned = now.astimezone()                      # system local zone
    candidate = now_zoned.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_zoned:
        candidate = (now_zoned + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)   # wall-clock arithmetic keeps DST correct
    return candidate


def parse_usage_limit(text: str, api_error_status: int | None, now: datetime | None = None) -> dict | None:
    """Detect a provider usage-limit failure and parse its reset time. Pure; never raises. Returns None when this is not a usage-limit message.
    Detected when api_error_status == 429 OR the text matches 'hit your ... limit'. reset_at_iso is None whenever the reset time is absent or unparseable
    (e.g. a date such as 'resets Nov 21, 2pm' is deliberately not guessed)."""
    text = text or ""
    if api_error_status != 429 and not _LIMIT_PHRASE.search(text):
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.astimezone()                            # naive = system local time
    kind_match = _LIMIT_KIND.search(text)
    info: dict = {"kind": kind_match.group(1) if kind_match else "unknown", "message": text, "api_error_status": api_error_status, "reset_text": None, "reset_at_iso": None}
    m = _RESET_CLOCK.search(text)
    if m:
        hour, minute, meridiem, tz_name = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
        if m.group(2) is None and not meridiem:
            return info                                   # a bare number ("resets 14") is not a time of day
        valid = (1 <= hour <= 12) if meridiem else (0 <= hour <= 23)
        if valid and minute <= 59:
            if meridiem:
                hour = hour % 12 + (12 if meridiem == "pm" else 0)
            info["reset_text"] = text[m.start(1):m.end()].strip()
            when = _next_wall_clock(hour, minute, tz_name, now)
            info["reset_at_iso"] = when.isoformat() if when else None
    return info


def usage_limit_wait_seconds(run_dir: Path, now: datetime | None = None, margin_s: float = 120) -> tuple[float | None, dict | None]:
    """How long to wait before resuming a run stopped by a usage limit: (seconds incl. margin, usage-limit info). (None, info|None) = reset time unknown.
    Source 1: run_dir/state.json["usage_limit"] (written by the controller, absolute reset_at_iso).
    Source 2 (runs recorded before this feature, e.g. improve-live-7/-8): the LAST errored model_reply in trace.jsonl, re-parsed. Its text carries only a wall-clock time,
    so it is anchored at the trace file's mtime (when the failure was written), not at 'now', otherwise a stale record would point at tomorrow's reset."""
    now = now or datetime.now(timezone.utc)
    info: dict | None = None
    state_path, trace_path = run_dir / "state.json", run_dir / "trace.jsonl"
    try:
        info = (json.loads(state_path.read_text()) or {}).get("usage_limit")
    except (OSError, json.JSONDecodeError):
        info = None
    if not info and trace_path.exists():
        last_error = None
        for line in trace_path.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "model_reply" and event.get("error"):
                last_error = event
        if last_error is not None:
            status_match = re.search(r"api_error_status=(\d+)", last_error["error"])
            anchor = datetime.fromtimestamp(trace_path.stat().st_mtime, tz=timezone.utc)
            info = parse_usage_limit(last_error.get("text") or last_error["error"], int(status_match.group(1)) if status_match else None, now=anchor)
    if not info or not info.get("reset_at_iso"):
        return None, info
    try:
        reset_at = datetime.fromisoformat(info["reset_at_iso"])
    except ValueError:
        return None, info
    return max(0.0, (reset_at - now).total_seconds() + margin_s), info


def wait_for(seconds: float, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print, chunk_s: float = 600.0) -> None:
    """Sleep `seconds` in chunks, reporting remaining time before each chunk. Makes NO API calls. `sleep` is injectable so tests never block."""
    remaining = float(seconds)
    while remaining > 0:
        log(f"waiting for usage-limit reset: {remaining / 60:.1f} min remaining")
        step = min(chunk_s, remaining)
        sleep(step)
        remaining -= step


@dataclass
class ModelReply:
    text: str
    elapsed_s: float = 0.0
    usage: dict | None = None
    cost_usd: float | None = None          # None = unknown
    cost_basis: str | None = None
    model: str | None = None
    error: str | None = None
    usage_limit: dict | None = None        # set (see parse_usage_limit) when the provider refused because of a subscription/usage limit; the controller does not charge such a call
    meta: dict = field(default_factory=dict)


class ModelProvider:
    name = "base"
    fixture = False

    def complete(self, system: str, transcript: str, timeout_s: float) -> ModelReply:
        raise NotImplementedError


class ScriptedProvider(ModelProvider):
    name = "scripted-fixture"
    fixture = True

    def __init__(self, script: list):
        self.script, self.i = list(script), 0

    def complete(self, system: str, transcript: str, timeout_s: float) -> ModelReply:
        if self.i >= len(self.script):
            return ModelReply("", error="script_exhausted")
        item = self.script[self.i]
        self.i += 1
        if isinstance(item, ModelReply):                  # fixture: a fully specified reply (e.g. a usage-limit failure) is returned unchanged
            return item
        text = item(transcript) if callable(item) else (item if isinstance(item, str) else json.dumps(item))
        return ModelReply(text, usage={"input_tokens": 0, "output_tokens": 0}, cost_usd=0.0, cost_basis="fixture (no model)", model="scripted-fixture")


class ClaudeCliProvider(ModelProvider):
    name = "claude-cli"

    def __init__(self, model: str | None = None, max_budget_usd: float = 0.5, binary: str = "claude"):
        self.model, self.max_budget_usd, self.binary = model, max_budget_usd, binary

    def command(self, system: str) -> list[str]:
        cmd = [self.binary, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
               "--max-budget-usd", str(self.max_budget_usd), "--system-prompt", system]
        return cmd + (["--model", self.model] if self.model else [])

    def complete(self, system: str, transcript: str, timeout_s: float) -> ModelReply:
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ae-agent-") as empty_cwd:           # the model has no files here and no tools
            try:
                proc = subprocess.run(self.command(system), input=transcript, capture_output=True, text=True, timeout=timeout_s, cwd=empty_cwd)
            except subprocess.TimeoutExpired:
                return ModelReply("", elapsed_s=time.monotonic() - start, error=f"model_timeout_{timeout_s}s")
            except FileNotFoundError:
                return ModelReply("", error="claude_cli_not_found")
        elapsed = time.monotonic() - start
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return ModelReply("", elapsed_s=elapsed, error=f"unreadable_provider_output (exit {proc.returncode}): {proc.stdout[:200]!r} {proc.stderr[:200]!r}")
        if data.get("is_error") or proc.returncode != 0:
            result_text, status = str(data.get("result", "")), data.get("api_error_status")
            limit = parse_usage_limit(result_text, status) or parse_usage_limit(proc.stderr or "", None)
            if limit:
                return ModelReply(result_text, elapsed_s=elapsed, error=f"provider_usage_limit: {data.get('result') or proc.stderr[:200]} (api_error_status={status})", usage_limit=limit, meta={"subtype": data.get("subtype")})
            return ModelReply(str(data.get("result", "")), elapsed_s=elapsed, error=f"provider_error: {data.get('result') or data.get('subtype') or proc.stderr[:200]} (api_error_status={data.get('api_error_status')})", meta={"subtype": data.get("subtype")})
        usage = data.get("usage") or {}
        mu = data.get("modelUsage") or {}
        return ModelReply(data.get("result", ""), elapsed_s=elapsed, usage={"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                          "cache_read_input_tokens": usage.get("cache_read_input_tokens"), "cache_creation_input_tokens": usage.get("cache_creation_input_tokens")},
                          cost_usd=data.get("total_cost_usd"), cost_basis=("list-basis estimate reported by the CLI; actual billing unknown" if data.get("total_cost_usd") is not None else None),
                          model=next(iter(mu), None), meta={"duration_api_ms": data.get("duration_api_ms"), "stop_reason": data.get("stop_reason")})

    def probe(self) -> ModelReply:
        return self.complete("Reply with exactly the word OK.", "ping", 60)
