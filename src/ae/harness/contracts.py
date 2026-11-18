"""Typed tool contracts: declared argument schemas, validation, structured results/errors. No tool is callable without a spec."""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}


@dataclass(frozen=True)
class Arg:
    type: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: dict[str, Arg]
    timeout_s: float = 30.0
    writes_workspace: bool = False

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "timeout_s": self.timeout_s,
                "args": {k: {"type": a.type, "required": a.required, "description": a.description} for k, a in self.args.items()}}


class ToolError(Exception):
    """Structured, machine-readable failure. codes: invalid_arguments | unknown_tool | timeout | stale_base | out_of_scope | malformed_patch | unreadable_result | internal"""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def validate_args(spec: ToolSpec, args: Any) -> dict:
    if not isinstance(args, dict):
        raise ToolError("invalid_arguments", f"{spec.name}: arguments must be an object, got {type(args).__name__}")
    unknown = set(args) - set(spec.args)
    if unknown:
        raise ToolError("invalid_arguments", f"{spec.name}: unknown argument(s) {sorted(unknown)}", {"allowed": sorted(spec.args)})
    clean = {}
    for name, a in spec.args.items():
        if name not in args:
            if a.required:
                raise ToolError("invalid_arguments", f"{spec.name}: missing required argument {name!r}")
            continue
        value = args[name]
        if a.type == "int" and isinstance(value, bool) or not isinstance(value, _TYPES[a.type]):
            raise ToolError("invalid_arguments", f"{spec.name}: argument {name!r} must be {a.type}, got {type(value).__name__}")
        clean[name] = value
    return clean


@dataclass
class ToolResult:
    tool: str
    ok: bool
    result: Any = None
    error: dict | None = None
    artifacts: list[dict] = field(default_factory=list)      # [{"path":..., "sha256":...}]
    elapsed_s: float = 0.0

    def to_json(self) -> dict:
        return {"tool": self.tool, "ok": self.ok, "result": self.result, "error": self.error, "artifacts": self.artifacts, "elapsed_s": round(self.elapsed_s, 3)}


class ToolRegistry:
    """Name -> (spec, handler). `call` NEVER raises: every failure becomes an explicit ToolResult(ok=False) (timeouts included, never success)."""

    def __init__(self):
        self._tools: dict[str, tuple[ToolSpec, Callable[..., Any]]] = {}

    def register(self, spec: ToolSpec, handler: Callable[..., Any]) -> None:
        self._tools[spec.name] = (spec, handler)

    def specs(self) -> list[ToolSpec]:
        return [s for s, _ in self._tools.values()]

    def call(self, name: str, args: Any) -> ToolResult:
        start = time.monotonic()
        if name not in self._tools:
            return ToolResult(name, False, error={"code": "unknown_tool", "message": f"no tool named {name!r}", "details": {"available": sorted(self._tools)}})
        spec, handler = self._tools[name]
        try:
            clean = validate_args(spec, args)
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(handler, **clean)
            try:
                out = future.result(timeout=spec.timeout_s)
            except concurrent.futures.TimeoutError:
                pool.shutdown(wait=False, cancel_futures=True)
                return ToolResult(name, False, error={"code": "timeout", "message": f"{name} exceeded {spec.timeout_s}s; result discarded (NOT a success)"}, elapsed_s=time.monotonic() - start)
            pool.shutdown(wait=False)
            artifacts = out.pop("_artifacts", []) if isinstance(out, dict) else []
            return ToolResult(name, True, result=out, artifacts=artifacts, elapsed_s=time.monotonic() - start)
        except ToolError as exc:
            return ToolResult(name, False, error={"code": exc.code, "message": exc.message, "details": exc.details}, elapsed_s=time.monotonic() - start)
        except Exception as exc:                                    # a crashing adapter is an error outcome, not a pass
            return ToolResult(name, False, error={"code": "internal", "message": f"{type(exc).__name__}: {exc}"}, elapsed_s=time.monotonic() - start)
