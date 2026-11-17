#!/usr/bin/env bash
# Reproduce the local toolchain. Idempotent. Needs: docker, curl, python3, uv. No cloud account, no API key.
# Downloads: pinned KiCad image, pinned Temurin JRE image, Freerouting 2.5.0 jar (sha256-checked), TI OPA2314 model zip (sha256-checked).
# The TI model's redistribution terms are unknown, so it is fetched into the gitignored models/cache/ and never committed.
set -euo pipefail
cd "$(dirname "$0")/.."
KICAD_IMG="kicad/kicad@sha256:3f4a01786dfbe803a65f345c3342b765db9add913f5e9448f8356793714ddc34"
JRE_IMG="eclipse-temurin@sha256:945247f9d092703e0c6be92243ef3c51640229e23ab34d353c8dcdc73e6cd4e6"
JAR_SHA="f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7"
TI_ZIP_SHA="3e0fc936312ab6de2d18fd33308bb71ed7d60b27f2f3bae7dbf7fc477ef0ca3b"

echo "== images"; docker pull "$KICAD_IMG" >/dev/null; docker pull "$JRE_IMG" >/dev/null
echo "== python env"; uv sync
echo "== KiCad libraries (host copy for the schematic generator and pin audit)"
if [ ! -d vendor/kicad-libs-10.0.8/symbols ]; then
  mkdir -p vendor/kicad-libs-10.0.8; cid=$(docker create "$KICAD_IMG")
  docker cp "$cid:/usr/share/kicad/symbols" vendor/kicad-libs-10.0.8/symbols
  docker cp "$cid:/usr/share/kicad/footprints" vendor/kicad-libs-10.0.8/footprints; docker rm "$cid" >/dev/null
fi
echo "== Freerouting 2.5.0 jar"
mkdir -p vendor/dl
if [ ! -f vendor/dl/freerouting-2.5.0.jar ]; then
  curl -sL -o vendor/dl/freerouting-2.5.0.jar https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar
fi
echo "$JAR_SHA  vendor/dl/freerouting-2.5.0.jar" | sha256sum -c -
echo "== TI OPA2314 model (rev C) -> ngspice shim"
mkdir -p models/cache/OPA2314
if [ ! -f models/cache/OPA2314/x/opa2314.lib ]; then
  curl -sL -A "Mozilla/5.0" -o models/cache/OPA2314/sbombu1a.zip https://www.ti.com/lit/zip/sbombu1
  echo "$TI_ZIP_SHA  models/cache/OPA2314/sbombu1a.zip" | sha256sum -c - || { echo "TI model archive changed: re-verify models/make_ngspice_shim.py before continuing"; exit 1; }
  (cd models/cache/OPA2314 && mkdir -p x && unzip -q -o sbombu1a.zip -d x)
fi
python3 models/make_ngspice_shim.py >/dev/null
echo "== datasheets are NOT fetched automatically (evidence/datasheets is gitignored); see evidence/pins/OPA2314AID.yaml for the exact file + sha256"
uv run ae tools check
