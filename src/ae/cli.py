"""`ae` command line: tools check | derive | run reference | defects | sims.

Examples:
  uv run ae tools check
  uv run ae run reference --out runs/ref/baseline
  uv run ae defects D1_swap_C1_C2_values
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ae.toolchain import FREEROUTING_JAR, FREEROUTING_JAR_SHA256, IMAGE, JRE_IMAGE, REPO_ROOT, run_in_image


def tools_check() -> int:
    """Print what is ACTUALLY installed/capable. Exit 1 if a required capability is missing (never silently pass)."""
    ok = True
    rows = []

    def row(name: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok &= good
        rows.append((name, "ok" if good else "MISSING", detail))

    docker = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True)
    row("docker", docker.returncode == 0, docker.stdout.strip() or docker.stderr.strip()[:80])
    if docker.returncode == 0:
        k = run_in_image("v", ["kicad-cli", "version"], REPO_ROOT)
        row("kicad-cli", k.exit_code == 0 and k.stdout.startswith("10."), k.stdout.strip())
        n = run_in_image("v", ["bash", "-c", "ngspice --version | grep -m1 -o 'ngspice-[0-9.]*'"], REPO_ROOT)
        row("ngspice", n.exit_code == 0 and "ngspice" in n.stdout, n.stdout.strip())
        p = run_in_image("v", ["python3", "/opt/ae/pcb_ops.py", "selftest"], REPO_ROOT)
        row("pcbnew DSN/SES API", "dsn export: True" in p.stdout, p.stdout.strip())
        j = subprocess.run(["docker", "run", "--rm", "--entrypoint", "java", JRE_IMAGE, "-version"], capture_output=True, text=True)
        row("java (router JRE)", j.returncode == 0, (j.stderr.splitlines() or [""])[0])
    import hashlib
    jar_ok = FREEROUTING_JAR.exists() and hashlib.sha256(FREEROUTING_JAR.read_bytes()).hexdigest() == FREEROUTING_JAR_SHA256
    row("freerouting jar", jar_ok, f"{FREEROUTING_JAR.name} sha256 {'matches' if jar_ok else 'MISSING/MISMATCH'} the recorded hash")
    model = REPO_ROOT / "models/cache/OPA2314/opa2314_ngspice_shim.lib"
    row("OPA2314 ngspice shim", model.exists(), "run scripts/bootstrap.sh to fetch the TI model and build the shim" if not model.exists() else str(model.relative_to(REPO_ROOT)))
    row("KiCad libs (host copy)", (REPO_ROOT / "vendor/kicad-libs-10.0.8/symbols").exists(), "extracted from the pinned image by scripts/bootstrap.sh")
    print(f"images: {IMAGE[:40]}..., {JRE_IMAGE[:40]}...")
    for name, status, detail in rows:
        print(f"  [{status:>7}] {name:<22} {detail}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ae", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tools"); t.add_argument("what", choices=["check"])
    sub.add_parser("derive", help="re-derive the filter from the requirements contract (about 2 min)")
    r = sub.add_parser("run"); r.add_argument("what", choices=["reference"]); r.add_argument("--out", default="runs/ref/baseline")
    d = sub.add_parser("defects"); d.add_argument("ids", nargs="*")
    args = ap.parse_args(argv)
    if args.cmd == "tools":
        return tools_check()
    if args.cmd == "derive":
        return subprocess.call([sys.executable, str(REPO_ROOT / "analysis/derive_filter.py")])
    if args.cmd == "run":
        from ae.reference_run import run_reference
        rec = run_reference(REPO_ROOT / args.out)
        for c in rec.checks:
            print(f"{c.id:<18}{c.outcome:<10}{c.detail[:100]}")
        print(json.dumps(rec.timings))
        return 0
    if args.cmd == "defects":
        return subprocess.call([sys.executable, "-m", "ae.defects", *args.ids])
    return 2


if __name__ == "__main__":
    sys.exit(main())
