"""Pinned toolchain image + a recorded way to run commands in it.

Every external-tool invocation goes through ``run_in_image`` so each stage gets
the same record: command, exit code, stdout/stderr, wall time, peak RSS.
Network is disabled by default: the tools must not need it, and this proves it.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# KiCad 10.0.8 official image, pinned by digest (resolved 2026-11-17).
# Ships KiCad 10.0.8, pcbnew SWIG module, ngspice 44.2 (Debian 13); verified by `ae tools check`.
IMAGE = "kicad/kicad@sha256:3f4a01786dfbe803a65f345c3342b765db9add913f5e9448f8356793714ddc34"
REPO_ROOT = Path(__file__).resolve().parents[2]
# Router: Freerouting 2.5.0 jar on a Temurin 25 JRE (class version 69 needs Java >= 25).
# The GraalVM-native `freerouting-cli` 2.5.0 was REJECTED: its routing thread dies with
# UnsupportedFeatureError (missing serialization config for ComponentOutline) and the process hangs
# forever. Evidence: docs/evidence/freerouting-native-cli-2.5.0-failure.log
JRE_IMAGE = "eclipse-temurin@sha256:945247f9d092703e0c6be92243ef3c51640229e23ab34d353c8dcdc73e6cd4e6"  # 25.0.5+8
FREEROUTING_JAR = REPO_ROOT / "vendor" / "dl" / "freerouting-2.5.0.jar"
# NOT in the publisher's checksums file (only the cli tarballs are); this is our own recorded hash.
FREEROUTING_JAR_SHA256 = "f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7"


@dataclass
class StageRecord:
    name: str
    cmd: list[str]
    exit_code: int
    wall_s: float
    peak_rss_mb: float | None
    stdout: str
    stderr: str
    outcome: str = field(default="")  # filled by callers: passed|failed|blocked|not_tested|error

    def to_json(self) -> dict:
        return asdict(self)


def _text(value: bytes | str | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def run_router(
    dsn: str, ses: str, workdir: Path, *, max_passes: int = 20, timeout_s: int = 300
) -> StageRecord:
    """Freerouting jar in the pinned JRE image. No network (it tries to send analytics otherwise).

    --hostname/--add-host: the JVM logger errors if the container hostname does not resolve.
    Peak memory comes from the container cgroup (memory.peak): the JRE image has no python/time.
    """
    container = f"ae-route-{uuid.uuid4().hex[:8]}"
    script = (
        f'java -jar /opt/fr/freerouting-2.5.0.jar --gui.enabled=false -de {dsn} -do {ses} -mp {max_passes}; '
        'rc=$?; echo "AE_MAXRSS_KB $(( $(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0) / 1024 ))" >&2; exit $rc'
    )
    cmd = ["docker", "run", "--rm", "--name", container, "--network", "none", "--hostname", "aebox",
           "--add-host", "aebox:127.0.0.1", "-u", "1000:1000", "-w", "/work",
           "-v", f"{workdir.resolve()}:/work:rw", "-v", f"{FREEROUTING_JAR.parent}:/opt/fr:ro",
           "--entrypoint", "sh", JRE_IMAGE, "-c", script]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        exit_code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "kill", container], capture_output=True)
        exit_code, out, err = 124, _text(exc.stdout), _text(exc.stderr) + "\nTIMEOUT"
    wall = time.monotonic() - started
    match = re.search(r"AE_MAXRSS_KB (\d+)", err)
    rss_mb = round(int(match.group(1)) / 1024, 1) if match and int(match.group(1)) else None
    err = re.sub(r"AE_MAXRSS_KB \d+\n?", "", err)
    return StageRecord("freerouting", cmd, exit_code, round(wall, 3), rss_mb, out, err)


def run_in_image(
    name: str,
    cmd: list[str],
    workdir: Path,
    *,
    network: bool = False,
    timeout_s: int = 600,
    extra_mounts: dict[Path, str] | None = None,
) -> StageRecord:
    """Run ``cmd`` inside the pinned image with ``workdir`` mounted at /work (rw)."""
    workdir = workdir.resolve()
    mounts = {
        workdir: "/work:rw",
        REPO_ROOT / "tools_kicad": "/opt/ae:ro",
        REPO_ROOT / "src": "/opt/src:ro",
        **(extra_mounts or {}),
    }
    container = f"ae-{name}-{uuid.uuid4().hex[:8]}"
    docker = ["docker", "run", "--rm", "--name", container, "-w", "/work", "-e", "HOME=/home/kicad", "-e", "PYTHONPATH=/opt/src"]
    if not network:
        docker += ["--network", "none"]
    for host, spec in mounts.items():
        docker += ["-v", f"{host}:{spec}"]
    docker += ["--entrypoint", "python3", IMAGE, "/opt/ae/measure.py", *cmd]
    started = time.monotonic()
    try:
        proc = subprocess.run(docker, capture_output=True, text=True, timeout=timeout_s)
        exit_code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        # Killing the docker CLI does NOT stop the container; kill our named container explicitly.
        subprocess.run(["docker", "kill", container], capture_output=True)
        exit_code, out, err = 124, _text(exc.stdout), _text(exc.stderr) + "\nTIMEOUT"
    wall = time.monotonic() - started
    match = re.search(r"AE_MAXRSS_KB (\d+)", err)
    rss_mb = int(match.group(1)) / 1024 if match else None
    err = re.sub(r"AE_MAXRSS_KB \d+\n?", "", err)
    return StageRecord(name, cmd, exit_code, round(wall, 3), rss_mb and round(rss_mb, 1), out, err)


def write_records(records: list[StageRecord], path: Path) -> None:
    path.write_text(json.dumps([r.to_json() for r in records], indent=2))


if __name__ == "__main__":
    rec = run_in_image("selftest", ["kicad-cli", "version"], REPO_ROOT)
    print(rec.exit_code, rec.stdout.strip(), f"{rec.wall_s}s", f"rss={rec.peak_rss_mb}MB")
    assert rec.exit_code == 0 and rec.stdout.startswith("10.0.8"), rec
