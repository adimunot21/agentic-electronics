"""Create the ngspice-44.2-compatible shim of TI's OPA2314 model (rev C) and record hashes.

Why: with the unmodified model, ngspice 44.2 never finds a DC operating point (dynamic gmin, true gmin and
source stepping all fail) and silently falls back to a short 'transient op' that does NOT settle slow RC nodes,
leaving bias nodes near 0 V (observed: VMID = 0.3 mV instead of 1.65 V) -> all AC results would be meaningless.
What: comment out exactly ONE line, the instance `X_SW_OR` (overload-recovery clamp switch cell). Nothing else changes.
Effect: overload/saturation recovery behaviour is no longer modelled. This project makes NO claim about it.
Verified (runs/*/model-check): DC converges directly; Vos ~0.48 mV (datasheet typ 0.5), Iq 150 uA/amp (datasheet 150 typ).
Run:  python models/make_ngspice_shim.py            (needs models/cache/OPA2314/x/opa2314.lib from the TI zip)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "models/cache/OPA2314/x/opa2314.lib"
DST = ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib"
EXPECTED_SRC_SHA256_CRLF = None  # recorded on first run in models/shim-record.json


def build() -> dict:
    raw = SRC.read_bytes()
    text = raw.decode("ascii").replace("\r\n", "\n")          # CRLF -> LF (ngspice needs LF)
    patched, count = re.subn(r"^(X_SW_OR\s.*)$", r"* NGSPICE-SHIM (patched out, see make_ngspice_shim.py): \1", text, flags=re.M)
    if count != 1:
        raise SystemExit(f"expected exactly one X_SW_OR instance, found {count}: model revision changed, re-verify the shim")
    DST.write_text(patched)
    record = {
        "source_file": str(SRC.relative_to(ROOT)), "source_sha256": hashlib.sha256(raw).hexdigest(),
        "shim_file": str(DST.relative_to(ROOT)), "shim_sha256": hashlib.sha256(DST.read_bytes()).hexdigest(),
        "changes": ["CRLF->LF", "comment out instance X_SW_OR (1 line)"],
        "not_modelled_after_shim": ["overload/saturation recovery clamp (SW_OR)"],
    }
    (ROOT / "models/shim-record.json").write_text(json.dumps(record, indent=2))
    return record


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
