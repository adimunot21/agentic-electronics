.PHONY: setup check test integration run defects derive
setup:        ## fetch/pin the toolchain (docker images, jar, TI model, KiCad libs)
	./scripts/bootstrap.sh
check:        ## print what is actually installed and capable
	uv run ae tools check
test:         ## offline unit tests (no docker needed except where marked)
	uv run pytest tests/unit -q
integration:  ## invoke the real kicad-cli / ngspice / freerouting in the pinned images
	uv run pytest tests/integration -q -m integration
run:          ## full reference run -> runs/ref/baseline/record.json (about 2 min)
	uv run ae run reference --out runs/ref/baseline
defects:      ## controlled defects vs predictions (about 6 min)
	uv run ae defects
derive:       ## re-derive the filter from the contract (about 2 min)
	uv run ae derive
