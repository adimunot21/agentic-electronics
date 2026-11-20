"""INDEPENDENT evaluator and acceptance policy.

The evaluator never reads anything the agent can write except the candidate manifest (as DATA). It runs the TRUSTED pipeline (ae.reference_run) from the repository code,
verifies the trusted-code digest is unchanged, and applies `decide` against the trusted pre-defect baseline.

ACCEPTANCE POLICY (defined before any repair attempt; do not edit after seeing a result):
 A candidate is ACCEPTED iff, comparing its FRESH full-pipeline record with the trusted pre-defect baseline record:
  1. every baseline check id is present in the candidate record (a missing/skipped check is never a pass);
  2. no check is worse than baseline (severity passed < blocked = not_tested < failed < error);
  3. no check is better than baseline (an unexplained improvement, e.g. REQ-STAB failed -> passed, is NOT accepted automatically: verdict `rejected: needs_review`);
  4. every numeric measurement matches baseline within rel_tol/abs_tol (raw-evidence comparison; this also covers checks that are unresolved/failed in the baseline, e.g. REQ-STAB:
     a candidate that moves their measurement cannot be assessed safely and is rejected);
  5. the trusted-code digest is unchanged before and after the evaluation, and the evaluation ran to completion (otherwise verdict `blocked`).
 Unresolved baseline failures stay listed in every decision; `release_ready` is ALWAYS False for a scoped repair.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ae.harness.circuit import CircuitProfile, resolve_callable
from ae.harness.workspace import REPO_ROOT, trusted_digest

SEVERITY = {"passed": 0, "blocked": 1, "not_tested": 1, "failed": 2, "error": 3}


@dataclass(frozen=True)
class AcceptancePolicy:
    rel_tol: float = 1e-6
    abs_tol: float = 1e-9
    allow_improvements: bool = False


def _by_id(checks: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for c in checks:
        cur = out.setdefault(c["id"], {"outcome": c["outcome"], "value": c.get("value"), "detail": c.get("detail", "")})
        if SEVERITY.get(c["outcome"], 3) > SEVERITY.get(cur["outcome"], 3):
            cur["outcome"], cur["detail"] = c["outcome"], c.get("detail", "")
        if cur["value"] is None and c.get("value") is not None:
            cur["value"] = c["value"]
    return out


def decide(baseline_checks: list[dict], candidate_checks: list[dict], policy: AcceptancePolicy = AcceptancePolicy()) -> dict:
    base, cand = _by_id(baseline_checks), _by_id(candidate_checks)
    violations = []
    for cid, b in base.items():
        if cid not in cand:
            violations.append({"id": cid, "kind": "missing_check", "baseline": b["outcome"], "candidate": None})
            continue
        c = cand[cid]
        sb, sc = SEVERITY.get(b["outcome"], 3), SEVERITY.get(c["outcome"], 3)
        if sc > sb:
            violations.append({"id": cid, "kind": "regression", "baseline": b["outcome"], "candidate": c["outcome"], "candidate_detail": c["detail"][:300]})
        elif sc < sb and not policy.allow_improvements:
            violations.append({"id": cid, "kind": "unexpected_improvement_needs_review", "baseline": b["outcome"], "candidate": c["outcome"]})
        if b["value"] is not None and c["value"] is not None and abs(c["value"] - b["value"]) > policy.abs_tol + policy.rel_tol * abs(b["value"]):
            violations.append({"id": cid, "kind": "measurement_drift", "baseline_value": b["value"], "candidate_value": c["value"], "delta": c["value"] - b["value"]})
    for cid in cand:
        if cid not in base:
            violations.append({"id": cid, "kind": "unknown_new_check", "candidate": cand[cid]["outcome"]})
    unresolved = sorted(cid for cid, b in base.items() if b["outcome"] != "passed")
    return {"verdict": "accepted" if not violations else "rejected", "violations": violations, "unresolved_baseline_failures": unresolved,
            "release_ready": False, "release_note": "a scoped repair is not a release pass; unresolved baseline failures remain and are listed"}


class TrustedEvaluator:
    def __init__(self, repo: Path = REPO_ROOT, policy: AcceptancePolicy = AcceptancePolicy(), profile: CircuitProfile | None = None, extra_globs: tuple = ()):
        self.repo, self.policy, self.profile = repo, policy, profile
        self.extra_globs = tuple(extra_globs) + (tuple(profile.extra_trusted_globs) if profile else ())      # () for FE: the digest is then exactly the pre-profile one
        self.digest_at_start = self._digest()

    def _digest(self) -> dict:
        return trusted_digest(self.repo, self.extra_globs) if self.extra_globs else trusted_digest(self.repo)

    def run_pipeline(self, components_path: Path, out_dir: Path) -> dict:
        if self.profile is not None and self.profile.pipeline:           # another circuit's trusted pipeline: (components_path, out_dir) -> record
            return resolve_callable(self.profile.pipeline)(components_path, out_dir)
        from ae.reference_run import run_reference            # trusted code, imported from the repository (digest-checked)
        run_reference(out_dir, label="harness-evaluation", components_path=components_path)
        return json.loads((out_dir / "record.json").read_text())

    def _guarded_run(self, components_path: Path, out_dir: Path, pipeline, start: float):
        """Run the trusted pipeline under the trusted-code digest guard. Returns (record, None) or (None, blocked_decision)."""
        before = self._digest()
        if before["combined"] != self.digest_at_start["combined"]:
            return None, {"verdict": "blocked", "reason": "trusted_code_changed_before_evaluation", "release_ready": False}
        try:
            record = (pipeline or self.run_pipeline)(components_path, out_dir)
        except Exception as exc:
            return None, {"verdict": "blocked", "reason": f"pipeline_error: {type(exc).__name__}: {exc}", "release_ready": False, "elapsed_s": time.monotonic() - start}
        after = self._digest()
        if after["combined"] != self.digest_at_start["combined"]:
            return None, {"verdict": "blocked", "reason": "trusted_code_changed_during_evaluation", "release_ready": False}
        return record, None

    def evaluate(self, components_path: Path, out_dir: Path, baseline_record: dict, pipeline=None) -> dict:
        start = time.monotonic()
        record, blocked = self._guarded_run(components_path, out_dir, pipeline, start)
        if blocked:
            return blocked
        d = decide(baseline_record["checks"], record["checks"], self.policy)
        d.update({"record_path": str(out_dir / "record.json"), "candidate_summary": record["summary"], "baseline_summary": baseline_record["summary"],
                  "candidate_inputs_sha256": record["inputs_sha256"], "trusted_digest": self.digest_at_start["combined"], "elapsed_s": round(time.monotonic() - start, 1)})
        return d

    def evaluate_improvement(self, components_path: Path, out_dir: Path, baseline_record: dict, baseline_dir: Path, policy, baseline_manifest: str, pipeline=None) -> dict:
        """IMPROVEMENT mode: same guarded fresh pipeline run, judged by the frozen policy (improvement.decide_improvement). The policy file is inside the trusted digest."""
        from ae.harness.improvement import decide_improvement
        start = time.monotonic()
        record, blocked = self._guarded_run(components_path, out_dir, pipeline, start)
        if blocked:
            return {**blocked, "mode": "improvement", "task": policy.task_id, "policy_sha256": policy.sha256, "policy_violations": [], "violations": [], "unresolved_baseline_failures": []}
        if record["inputs_sha256"].get("components") != hashlib.sha256(components_path.read_bytes()).hexdigest():
            return {"verdict": "blocked", "reason": "stale_or_foreign_evidence: record input hash != candidate manifest", "mode": "improvement", "release_ready": False, "policy_violations": [], "violations": [], "unresolved_baseline_failures": []}
        d = decide_improvement(baseline_record, record, policy, baseline_dir, out_dir, baseline_manifest, components_path.read_text())
        d.update({"record_path": str(out_dir / "record.json"), "candidate_summary": record["summary"], "baseline_summary": baseline_record["summary"],
                  "baseline_inputs_sha256": baseline_record["inputs_sha256"], "candidate_inputs_sha256": record["inputs_sha256"],
                  "trusted_digest": self.digest_at_start["combined"], "elapsed_s": round(time.monotonic() - start, 1)})
        return d
