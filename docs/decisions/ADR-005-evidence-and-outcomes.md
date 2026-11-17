# ADR-005 Outcomes and evidence binding
Outcomes: `passed | failed | blocked | not_tested | error`. A missing tool/model, a diagnosed fallback, or a skipped check is never `passed`.
Every `record.json` stores the exact commands, exit codes, wall time, peak memory, stdout/stderr tails, tool versions, image digests and sha256 of all inputs.
`ae.evidence.stale_reasons` lists inputs whose hash changed since a result was produced; demonstrated with patch 0001 (components, netlist, schematic became stale; model, pin evidence, contract did not).
Contract changes are logged (`revisions:`) with `made_after_result` and an independence caveat. Defect predictions are committed *before* the runs (git history) and compared mechanically (`ae.defects`).
