"""Model providers. A provider turns (system prompt, transcript text) into a reply plus honest usage information.

ClaudeCliProvider: the logged-in `claude` CLI in headless mode with ALL built-in tools disabled, no session persistence, no settings/MCP, run from an empty temp cwd.
  Verified in this environment 2026-11-18 (model claude-sonnet-5-5, auth: claude.ai). Cost reported by the CLI is a LIST-BASIS ESTIMATE; real billing is unknown (None => unknown, never 0).
ScriptedProvider: FIXTURE for deterministic controller tests. Its runs are labelled `fixture=True` everywhere and never count as agent success.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ModelReply:
    text: str
    elapsed_s: float = 0.0
    usage: dict | None = None
    cost_usd: float | None = None          # None = unknown
    cost_basis: str | None = None
    model: str | None = None
    error: str | None = None
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
            return ModelReply(str(data.get("result", "")), elapsed_s=elapsed, error=f"provider_error: {data.get('result') or data.get('subtype') or proc.stderr[:200]} (api_error_status={data.get('api_error_status')})", meta={"subtype": data.get("subtype")})
        usage = data.get("usage") or {}
        mu = data.get("modelUsage") or {}
        return ModelReply(data.get("result", ""), elapsed_s=elapsed, usage={"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                          "cache_read_input_tokens": usage.get("cache_read_input_tokens"), "cache_creation_input_tokens": usage.get("cache_creation_input_tokens")},
                          cost_usd=data.get("total_cost_usd"), cost_basis=("list-basis estimate reported by the CLI; actual billing unknown" if data.get("total_cost_usd") is not None else None),
                          model=next(iter(mu), None), meta={"duration_api_ms": data.get("duration_api_ms"), "stop_reason": data.get("stop_reason")})

    def probe(self) -> ModelReply:
        return self.complete("Reply with exactly the word OK.", "ping", 60)
