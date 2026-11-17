import json, numpy as np
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ae import sim_metrics as sm
W=Path("runs/ref/stability/work"); R=json.load(open("runs/ref/stability/stability_results.json"))
fig,ax=plt.subplots(2,2,figsize=(14,9))
for tag,name,c in (("stage1","stage 1 MFB (U1A)","tab:blue"),("stage2","stage 2 1st-order (U1B)","tab:red")):
    f=[p for p in W.glob("lg_*_T.csv") if tag in p.name][0]
    d=np.genfromtxt(f,delimiter=",",names=True); key=[k for k in R["loop_gain"] if k.startswith(tag)][0]; m=R["loop_gain"][key]
    ax[0,0].semilogx(d["f_Hz"],d["T_dB"],c,label=f"{name}: fc={m['crossover_hz']/1e6:.2f} MHz, PM={m['phase_margin_deg']:.1f} deg, GM={m['gain_margin_dB']:.1f} dB")
    ax[0,1].semilogx(d["f_Hz"],d["T_phase_deg"],c,label=name)
ax[0,0].axhline(0,color="k",lw=.8); ax[0,0].set_ylim(-40,110); ax[0,0].set_title("Loop gain |T| (series injection at the op-amp input pin)"); ax[0,0].set_ylabel("dB"); ax[0,0].legend(fontsize=8); ax[0,0].grid(True,which="both",alpha=.25)
ax[0,1].axhline(-180,color="k",ls="--",lw=.8); ax[0,1].set_title("Loop phase"); ax[0,1].set_ylabel("deg"); ax[0,1].legend(fontsize=8); ax[0,1].grid(True,which="both",alpha=.25)
t,(v,)=sm.read_wrdata(W/"st_real.csv",1,False); ti,(vi,)=sm.read_wrdata(W/"st_ideal.csv",1,False)
v0=v[t<100e-6].mean(); vi0=vi[ti<100e-6].mean(); a=ax[1,0]
a.plot(t*1e6,(v-v0)*1e3,"tab:green",label="real (TI model)"); a.plot(ti*1e6,(vi-vi0)*1e3,"k--",lw=1,label="ideal op-amps (same netlist)"); a.set_xlim(80,1500)
d=a.twinx(); d.plot(t*1e6,((v-v0)-np.interp(t,ti,vi-vi0))*1e3,"tab:orange",lw=.9,alpha=.9); d.set_ylabel("real - ideal (mV, orange)",color="tab:orange")
a.set_title("Input step 0.2 V at source: AOUT (overshoot real %.1f%% vs ideal %.1f%%; max |real-ideal| %.2f mV)"%(R["input_step"]["overshoot_pct_sim"],R["input_step"]["overshoot_pct_ideal"],R["input_step"]["max_abs_diff_mV"]),fontsize=9); a.set_xlabel("us"); a.set_ylabel("AOUT change (mV)"); a.legend(fontsize=8,loc="lower right"); a.grid(alpha=.25)
t,(v,)=sm.read_wrdata(W/"ld_real.csv",1,False); ti,(vi,)=sm.read_wrdata(W/"ld_ideal.csv",1,False); b=ax[1,1]
b.plot(t*1e6,(v-v[t<18e-6].mean())*1e3,"tab:green",label="real (TI model)"); b.plot(ti*1e6,(vi-vi[ti<18e-6].mean())*1e3,"k--",lw=1,label="ideal op-amps"); b.set_xlim(19,24)
b.set_title("Load step: 50 uA sink on AOUT at 20 us (overshoot beyond static: real %.2f mV, ideal 0)"%R["load_step"]["real"]["overshoot_beyond_static_mV"],fontsize=9); b.set_xlabel("us"); b.set_ylabel("AOUT change (mV)"); b.legend(fontsize=8); b.grid(alpha=.25)
fig.suptitle("Stability EVIDENCE on the adopted baseline. REQ-STAB rev 2 (in force) = FAILED; rev 3 PROPOSED, NOT APPROVED. No thresholds adopted.",fontsize=11,color="darkred")
plt.tight_layout(rect=(0,0,1,.96)); plt.savefig("docs/figures/stability_evidence.png",dpi=95)
