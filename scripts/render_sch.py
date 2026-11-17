"""Regenerate the dev schematic, run ERC, export PDF->PNG crops for visual inspection. Usage: render_sch.py"""
import shutil, subprocess
from pathlib import Path
from PIL import Image
from ae.designs import fe_aaf_001 as d
from ae.ownership import guarded_write
from ae.stages import kicad_erc, kicad_netlist, compare_netlist_to_intent
from ae.toolchain import run_in_image
W = Path("runs/ref/dev"); shutil.rmtree(W, ignore_errors=True); W.mkdir(parents=True)
guarded_write(W / "fe_aaf_001.kicad_sch", d.build().to_text(), W / ".generated.json")
rec, rep = kicad_erc(W, "fe_aaf_001.kicad_sch"); print("ERC", rec.outcome, [v["description"] for v in rep["violations"]][:5])
kicad_netlist(W, "fe_aaf_001.kicad_sch", "fe_aaf_001.net"); print(compare_netlist_to_intent(W / "fe_aaf_001.net", d.intended_nets()))
run_in_image("pdf", ["kicad-cli", "sch", "export", "pdf", "-o", "sch.pdf", "fe_aaf_001.kicad_sch"], W)
subprocess.run(["pdftoppm", "-png", "-r", "220", "sch.pdf", "sch"], cwd=W, check=True)
im = Image.open(W / "sch-1.png"); w, h = im.size
im.crop((0, int(h*.08), int(w*.55), int(h*.45))).save(W / "crop_stage1.png")
im.crop((int(w*.45), int(h*.08), w, int(h*.62))).save(W / "crop_right.png")
im.crop((0, int(h*.35), int(w*.55), int(h*.78))).save(W / "crop_bottom.png")
