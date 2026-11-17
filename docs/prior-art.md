# Prior art inspected (2026-11-17, via GitHub API and file trees; READMEs not trusted for capability)

| Project | Revision | Licence (verified) | What it is (from code layout) | Used here? |
|---|---|---|---|---|
| unmateria/mcp-kicad | 9d0332e (v0.2.8) | PolyForm Noncommercial 1.0.0 (`LICENSE.md`) | Go MCP server; **now has `internal/pcbgen` and a kicad-cli DRC wrapper**, so the "schematic-only" description is outdated | No (licence blocks a commercial path) |
| PranavAchar01/etch | c548129 | **No licence file** (all rights reserved by default) | Python agent + KiCad 10/ngspice in Docker | No — ideas only, nothing copied |
| jefcoder/circuitron | ac42a03 | MIT | OpenAI-Agents-SDK pipeline, SKiDL scripts, KiCad in Docker, ERC | No |
| atopile/atopile | 619eda7 (v0.14.x) | MIT | `.ato` language, KiCad layout sync, parts picker, agent routes in server | No (candidate alternative representation; revisit) |
| Freerouting | v2.5.0 | GPL-3.0 | autorouter | Yes, as a separate process only (no linking) |
| KiCad | 10.0.8 | GPL-3.0 (libs CC-BY-SA-4.0 + exception) | EDA suite | Yes, via CLI/SWIG in a container |
| ngspice | 44.2 | BSD-3-style (Debian pkg) | simulator | Yes |
| LTspice | — | Proprietary freeware | — | No |
Obligation notes: scripts that `import pcbnew` run only inside the KiCad image and are kept in `tools_kicad/`.
Vendor SPICE models carry their own licences and are tracked per model in `models/manifest.yaml`.
