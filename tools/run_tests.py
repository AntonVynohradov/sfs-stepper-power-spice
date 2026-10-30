#!/usr/bin/env python3
################################################################################
# sfs-stepper-power-spice: power stage models (ngspice)
#
# Description: Runs the ngspice test benches of the B-G431B-ESC1 power stage,
#              checks them (SP01 device, SP02 dead time, SP03 shunt), and exports
#              the parameters of the switching stage model used by sfs-stepper-sim.
#
# Author:      Anton Vynohradov
# Email:       avynohradov@systemfromscratch.com
# License:     Apache License 2.0
# Copyright:   (c) 2026 Anton Vynohradov
#
# Usage:       python tools/run_tests.py [--quick] [--export] [--sim DIR]
#              NGSPICE=<path to ngspice / ngspice_con.exe> if not in PATH
#
# SPDX-License-Identifier: Apache-2.0
#
################################################################################

import argparse
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")

# Operating point and board values. Values marked "assumption" are not published in
# the B-G431B-ESC1 user manual; take them from the schematic (MB1419) when available.
BOARD = dict(
    vbus=24.0,        # V, laboratory supply (as in sfs-stepper-sim)
    fs=20000.0,       # Hz, PWM frequency
    dead_ns=500.0,    # ns, dead time of the timer (assumption, firmware setting)
    vcc=12.0,         # V, gate driver supply (assumption)
    rg=10.0,          # Ohm, gate resistors (assumption)
    cboot=1e-6,       # F, bootstrap capacitor (assumption)
    rsh=3e-3,         # Ohm, low-side shunt (assumption)
    lsh=1e-9,         # H, shunt + layout inductance (assumption)
    lloop=3e-9,       # H, commutation loop inductance (assumption)
    tj=25.0,          # degC, junction temperature
)

# STL180N6F7 datasheet (typical unless noted)
DATASHEET = dict(rds_on=1.9e-3, vsd_max=1.2, ciss=4825e-12, coss=2240e-12, crss=216e-12,
                 qg=79.5e-9)


# ------------------------------------------------------------------------------
# ngspice
# ------------------------------------------------------------------------------

def ngspice_exe():
    exe = os.environ.get("NGSPICE")
    if exe:
        return exe
    for name in ("ngspice_con", "ngspice"):
        path = shutil.which(name)
        if path:
            return path
    sys.exit("ngspice not found: put it in PATH or set NGSPICE (README: Requirements)")


def spice_path(p):
    return p.replace("\\", "/")


def includes():
    return "\n".join(f".include \"{spice_path(os.path.join(ROOT, f))}\"" for f in
                     ("models/stl180n6f7_sfs.lib", "models/l6387_behav.lib",
                      "circuits/esc1_leg.lib"))


def run(name, netlist):
    """Runs a netlist in batch mode. The control block writes <name>.dat (wrdata) or
    prints 'name = value' lines; returns (stdout, data or None)."""
    os.makedirs(BUILD, exist_ok=True)
    cir = os.path.join(BUILD, name + ".cir")
    dat = os.path.join(BUILD, name + ".dat")
    if os.path.exists(dat):
        os.remove(dat)
    with open(cir, "w", newline="\n") as f:
        f.write(netlist.replace("@DAT@", spice_path(dat)))
    r = subprocess.run([ngspice_exe(), "-b", cir], capture_output=True, text=True,
                       cwd=BUILD, timeout=900)
    if r.returncode != 0 or "aborted" in r.stdout + r.stderr:
        sys.exit(f"ngspice failed on {name}:\n{r.stderr[-2000:]}\n{r.stdout[-2000:]}")
    data = np.loadtxt(dat) if os.path.exists(dat) else None
    return r.stdout, data


def values(stdout):
    out = {}
    for line in stdout.splitlines():
        parts = line.split("=")
        if len(parts) == 2:
            try:
                out[parts[0].strip()] = float(parts[1].split()[0])
            except (ValueError, IndexError):
                pass
    return out


def check(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
    return ok


# ------------------------------------------------------------------------------
# SP01: device against the datasheet
# ------------------------------------------------------------------------------

def sp01(b):
    ok = True
    ds = DATASHEET
    # Rds(on) at VGS = 10 V, ID = 16 A; VSD at 32 A; at 25 and 100 degC
    res = {}
    for tj in (25.0, 100.0):
        net = f"""* SP01 static
{includes()}
VG g 0 10
ID 0 d DC 16
M1 d g 0 STL180N6F7_SFS temp={tj}
VG2 g2 0 0
ID2 0 d2 DC -32
M2 d2 g2 0 STL180N6F7_SFS temp={tj}
ID3 0 d3 DC -2
M3 d3 g2 0 STL180N6F7_SFS temp={tj}
.control
op
let rds = v(d) / 16
print rds
let vsd32 = -v(d2)
let vsd2 = -v(d3)
print vsd32 vsd2
.endc
.end
"""
        v = values(run(f"sp01_static_{int(tj)}", net)[0])
        res[tj] = v
    rds = res[25.0]["rds"]
    ok &= check("SP01 Rds(on) at VGS 10 V, 16 A, 25 degC", abs(rds / ds["rds_on"] - 1) < 0.05,
                f"{rds * 1e3:.3f} mOhm (datasheet typ {ds['rds_on'] * 1e3:.1f}), "
                f"100 degC: {res[100.0]['rds'] * 1e3:.3f} mOhm "
                f"(x{res[100.0]['rds'] / rds:.2f})")
    vsd = res[25.0]["vsd32"]
    ok &= check("SP01 body diode VSD at 32 A", 0.8 < vsd <= ds["vsd_max"],
                f"{vsd:.3f} V (datasheet max {ds['vsd_max']} V), at 2 A {res[25.0]['vsd2']:.3f} V")

    # capacitances at VDS = 25 V, VGS = 0, 1 MHz
    net = f"""* SP01 capacitances
{includes()}
* Ciss: AC on the gate, drain AC-grounded
VD1 d1 0 DC 25
VG1 g1 0 DC 0 AC 1
M1 d1 g1 0 STL180N6F7_SFS
* Crss: AC on the drain, current into the AC-grounded gate
VD2 d2 0 DC 25 AC 1
VG2 g2 0 DC 0
M2 d2 g2 0 STL180N6F7_SFS
* Coss: AC on the drain, gate shorted to source
VD3 d3 0 DC 25 AC 1
M3 d3 0 0 STL180N6F7_SFS
.control
ac lin 1 1Meg 1Meg
let w = 2 * pi * 1e6
let ciss = abs(imag(i(VG1))) / w
let crss = abs(imag(i(VG2))) / w
let coss = abs(imag(i(VD3))) / w
print ciss crss coss
.endc
.end
"""
    v = values(run("sp01_cap", net)[0])
    for key in ("ciss", "coss", "crss"):
        ok &= check(f"SP01 {key.capitalize()} at VDS 25 V", abs(v[key] / ds[key] - 1) < 0.1,
                    f"{v[key] * 1e12:.0f} pF (datasheet typ {ds[key] * 1e12:.0f} pF)")

    # gate charge: VDD 30 V, ID 32 A (clamped inductive load), IG 1 mA
    net = f"""* SP01 gate charge
{includes()}
VDD dd 0 30
IL dd d DC 32
DCL d dd DFW
.model DFW D IS=1e-12 RS=1m
IG 0 g PULSE(0 1m 1u 1n 1n 1 2)
M1 d g 0 STL180N6F7_SFS
.control
tran 5n 150u
meas tran t10 WHEN v(g)=10 RISE=1
let qg = (t10 - 1u) * 1m
print qg
.endc
.end
"""
    v = values(run("sp01_qg", net)[0])
    ok &= check("SP01 gate charge Qg (0..10 V, 30 V, 32 A)", abs(v["qg"] / ds["qg"] - 1) < 0.1,
                f"{v['qg'] * 1e9:.1f} nC (datasheet typ {ds['qg'] * 1e9:.1f} nC)")
    return ok, dict(rds_on=rds, rds_on_100=res[100.0]["rds"], vsd_2a=res[25.0]["vsd2"],
                    vsd_32a=vsd)


# ------------------------------------------------------------------------------
# SP02 / SP03: switching leg
# ------------------------------------------------------------------------------

def leg_bench(name, b, d, iload, periods, tmax="5n", extra=""):
    """One leg, centre-aligned PWM as in the sim model: period from counter peak to
    counter peak, high side ideally on in [Ts/2 (1 - d), Ts/2 (1 + d)], every turn-on
    delayed by the timer dead time. Load: phase current as a current source (the
    phase inductance is 3 mH: almost constant within a period)."""
    ts = 1.0 / b["fs"]
    td = b["dead_ns"] * 1e-9
    t1 = ts / 2 * (1 - d)
    t2 = ts / 2 * (1 + d)
    net = f"""* {name}
{includes()}
VBUS bus 0 {b['vbus']}
VCC vcc 0 {b['vcc']}
VH hin 0 PULSE(0 3.3 {t1 + td} 2n 2n {t2 - t1 - td - 2e-9} {ts})
VLP lp 0 PULSE(0 3.3 {t1} 2n 2n {t2 + td - t1 - 2e-9} {ts})
BLIN lin 0 V = 3.3 - V(lp)
X1 bus 0 out hin lin vcc sh ESC1_LEG params: rg={b['rg']} cboot={b['cboot']} rsh={b['rsh']}
+ lsh={b['lsh']} lloop={b['lloop']} tj={b['tj']}
IL out 0 DC {iload}
* absolute tolerances suited to amperes and volts (the defaults stall the solver on
* the quiet gate nodes)
.options abstol=1e-9 vntol=1e-5
.control
tran 1n {periods * ts} {(periods - 2) * ts} {tmax}
let vgsh = v(x1.gh) - v(out)
let vgsl = v(x1.gl) - v(x1.sl)
wrdata @DAT@ v(out) v(sh) vgsh vgsl
.endc
.end
"""
    _, data = run(name, net)
    return data


def period_mean(t, y, ts):
    t0 = t[-1] - ts
    sel = t >= t0 - 1e-15
    return np.trapezoid(y[sel], t[sel]) / (t[sel][-1] - t[sel][0])


def edge_delays(t, v, b, d, i):
    """Delays of the switch node edges (crossing Vbus / 2) in the last period, from the
    ideal edges Ts/2 (1 - d) (rising) and Ts/2 (1 + d) (falling)."""
    ts = 1.0 / b["fs"]
    t0 = t[-1] - ts
    sel = t >= t0
    tt, vv = t[sel] - t0, v[sel]
    half = b["vbus"] / 2
    up = np.where((vv[:-1] < half) & (vv[1:] >= half))[0]
    dn = np.where((vv[:-1] >= half) & (vv[1:] < half))[0]
    t_up = tt[up[0]] - ts / 2 * (1 - d) if len(up) else float("nan")
    t_dn = tt[dn[0]] - ts / 2 * (1 + d) if len(dn) else float("nan")
    return t_up, t_dn


def gate_timing(t, vgsh, vgsl, vth, hold=100e-9):
    """Dead time margin and induced gate bump in one period.

    margin: shortest time from one gate falling below vth to the other gate rising
            above vth and staying there for at least `hold` (driven turn-on);
    bump:   highest gate voltage of a switch that is not driven on (gate pulled up
            by Cgd dv/dt when the other switch turns on)."""
    gaps = []
    bump = 0.0
    for a, bb in ((vgsh, vgsl), (vgsl, vgsh)):
        off = np.where((a[:-1] >= vth) & (a[1:] < vth))[0]
        up = np.where((bb[:-1] < vth) & (bb[1:] >= vth))[0]
        driven = [k for k in up if np.all(bb[(t > t[k]) & (t < t[k] + hold)] >= vth)]
        for k in off:
            later = [j for j in driven if j > k]
            if later:
                gaps.append(t[later[0]] - t[k])
    on_h = vgsh > vth
    on_l = vgsl > vth
    for a, other_on in ((vgsl, on_h), (vgsh, on_l)):
        # off switch: windows within 200 ns of the other being on, excluding its own
        # driven on time (a above vth for longer than hold)
        sel = other_on
        if np.any(sel):
            bump = max(bump, float(np.max(a[sel])))
    return (min(gaps) if gaps else float("nan")), bump


def sp02(b, dev, quick):
    """Mean leg voltage error against the ideal d * Vbus, as a function of the phase
    current, and the parameters of the sim switching model
    error = -sign(i) (Vbus + 2 Vd) td_eff / Ts - R i, R = (Rds + Rds + Rshunt) / 2,
    with td_eff fitted for the working range 1 A <= |i| <= 3 A (smallest largest
    deviation)."""
    ts = 1.0 / b["fs"]
    d = 0.5
    currents = [-3, -2, -1, -0.5, -0.25, 0.25, 0.5, 1, 2, 3]
    if quick:
        currents = [-2, -1, 1, 2]
    rows = []
    delays = {}
    margins = []
    for i in currents:
        data = leg_bench(f"sp02_i{i:+.2f}", b, d, i, 6, tmax="2n")
        t, vout = data[:, 0], data[:, 1]
        err = period_mean(t, vout, ts) - d * b["vbus"]
        delays[i] = edge_delays(t, vout, b, d, i)
        sel = t >= t[-1] - ts
        margins.append(gate_timing(t[sel], data[sel, 5], data[sel, 7], 3.0))
        rows.append((i, err))
        print(f"      i = {i:+5.2f} A: leg voltage error {err * 1e3:+8.2f} mV, switch node edges "
              f"+{delays[i][0] * 1e9:.0f} ns (rising), +{delays[i][1] * 1e9:.0f} ns (falling) "
              f"after the ideal edges", flush=True)
    rows = np.array(rows)
    vd = dev["vsd_2a"]
    r_leg = (2 * dev["rds_on"] + b["rsh"]) / 2
    k = (b["vbus"] + 2 * vd) / ts
    big = np.abs(rows[:, 0]) >= 1.0
    need = (np.abs(rows[big, 1]) - np.abs(rows[big, 0]) * r_leg) / k
    td_eff = float((np.min(need) + np.max(need)) / 2)
    model = -np.sign(rows[:, 0]) * k * td_eff - rows[:, 0] * r_leg
    dev_big = np.max(np.abs(rows[big, 1] - model[big]))
    ok = check("SP02 sim model (step at the current sign) within 15 mV for 1 A <= |i| <= 3 A",
               dev_big < 15e-3 and 0 < td_eff,
               f"td_eff = {td_eff * 1e9:.0f} ns (timer {b['dead_ns']:.0f} ns), "
               f"R = {r_leg * 1e3:.2f} mOhm, Vd = {vd:.3f} V, max deviation "
               f"{dev_big * 1e3:.2f} mV")
    margin = min(m for m, _ in margins)
    bump = max(bm for _, bm in margins)
    ok &= check("SP02 dead time covers the turn-off: gap between the driven gates "
                "(VGS(th) 3 V)", margin > 0,
                f"smallest gap {margin * 1e9:.0f} ns with a timer dead time of "
                f"{b['dead_ns']:.0f} ns (the rest is the turn-off delay)")
    print(f"      NOTE: the gate of the switch that is off rises to {bump:.2f} V while the other "
          f"turns on (Cgd dv/dt against the gate resistor); above VGS(th) a short "
          f"shoot-through follows - check Rg / turn-off diode on the real board")
    print("      error / model: " + ", ".join(f"{i:+.2f} A: {e / m:.2f}"
                                             for i, e, m in zip(rows[:, 0], rows[:, 1], model)))
    print("      (below 1 A the phase current charges the switch node capacitance too slowly "
          "to finish the swing within the dead time: the error shrinks towards zero current)")
    d2 = delays.get(2.0, (float("nan"), float("nan")))
    return ok, dict(td_eff=td_eff, r_leg=r_leg, vd=vd, gate_gap=margin, gate_bump=bump, delay_rise_2a=d2[0],
                    delay_fall_2a=d2[1], table=[[float(i), float(e)] for i, e in rows])


def sp03(b, quick):
    """Low-side shunt: value at the sampling instant (counter peak = start of the
    period, middle of the low-side on time) and settling after the low-side turn-on."""
    ts = 1.0 / b["fs"]
    ok = True
    settle = 0.0
    cases = [(0.5, 2.0), (0.5, -2.0), (0.9, 2.0)]
    if quick:
        cases = [(0.5, 2.0)]
    for d, i in cases:
        data = leg_bench(f"sp03_d{d:.2f}_i{i:+.1f}", b, d, i, 4, tmax="1n")
        t, vsh = data[:, 0], data[:, 3]
        t0 = t[-1] - ts                      # start of the last period (sample instant)
        ideal = -i * b["rsh"]                # current out of the leg flows up the shunt
        k0 = np.argmin(np.abs(t - t0))
        sample = vsh[k0]
        # settling, counted from the timer edge that turns the low side on (includes the
        # driver and switch delays)
        t_on = t0 - ts / 2 * (1 - d) + b["dead_ns"] * 1e-9   # low side turned on
        sel = (t >= t_on) & (t <= t0)
        tt, vv = t[sel], vsh[sel]
        outside = np.where(np.abs(vv - ideal) > 0.02 * abs(ideal))[0]
        ts_settle = (tt[outside[-1]] - t_on) if len(outside) else 0.0
        settle = max(settle, ts_settle)
        ok &= check(f"SP03 shunt at the sampling instant, d = {d}, i = {i:+.1f} A",
                    abs(sample - ideal) < 0.01 * abs(ideal),
                    f"{sample * 1e3:+.3f} mV (ideal {ideal * 1e3:+.3f} mV), settles to 2 % "
                    f"{ts_settle * 1e9:.0f} ns after the low-side command")
    t_adc = 250e-9          # ADC sampling window of the STM32G4 (assumption)
    d_max = 1 - 2 * (settle + t_adc + b["dead_ns"] * 1e-9) / ts
    print(f"      largest leg duty with a valid sample (settling {settle * 1e9:.0f} ns + "
          f"ADC {t_adc * 1e9:.0f} ns before the sampling instant): {d_max:.3f}")
    return ok, dict(shunt_settle=settle, duty_max_sampling=d_max)


# ------------------------------------------------------------------------------
# export
# ------------------------------------------------------------------------------

def export(b, dev, sw, sh, sim_dir):
    stage = dict(
        source="sfs-stepper-power-spice tools/run_tests.py (ngspice, models/*.lib)",
        board=dict(b),
        rds_on=dev["rds_on"],
        rds_on_100c=dev["rds_on_100"],
        vd=sw["vd"],
        dead_timer_ns=b["dead_ns"],
        dead_eff_ns=sw["td_eff"] * 1e9,
        r_leg=sw["r_leg"],
        delay_rise_2a_ns=sw["delay_rise_2a"] * 1e9,
        delay_fall_2a_ns=sw["delay_fall_2a"] * 1e9,
        gate_gap_ns=sw["gate_gap"] * 1e9,
        gate_bump_v=sw["gate_bump"],
        leg_error_table=sw["table"],
        shunt_settle_ns=sh["shunt_settle"] * 1e9,
        duty_max_sampling=sh["duty_max_sampling"],
    )
    stage = {k: (float(v) if isinstance(v, (float, np.floating)) else v)
             for k, v in stage.items()}
    os.makedirs(os.path.join(ROOT, "export"), exist_ok=True)
    path = os.path.join(ROOT, "export", "stage_esc1.json")
    with open(path, "w", newline="\n") as f:
        json.dump(stage, f, indent=2)
        f.write("\n")
    print(f"wrote {os.path.relpath(path, ROOT)}")
    sci = f"""// =============================================================================
// sfs-stepper-sim: Stepper motor model and control simulation (Scilab/Xcos)
//
// File:        params/stage_esc1_spice.sci
// Description: Switching stage parameters of the B-G431B-ESC1 from ngspice.
//              GENERATED by sfs-stepper-power-spice/tools/run_tests.py --export:
//              do not edit, change the ngspice models or the board values there.
//
// Author:      Anton Vynohradov
// Email:       avynohradov@systemfromscratch.com
// License:     Apache License 2.0
// Copyright:   (c) 2026 Anton Vynohradov
//
// SPDX-License-Identifier: Apache-2.0
// =============================================================================

function stg = stage_esc1_spice()
    stg = struct();
    stg.rds_on        = {stage['rds_on']!r};   // Ohm, switch on-resistance, 25 degC
    stg.r_leg         = {stage['r_leg']!r};   // Ohm, mean leg resistance (high side, low side + shunt)
    stg.vd            = {stage['vd']!r};        // V, body diode at 2 A
    stg.dead_timer_ns = {stage['dead_timer_ns']!r};          // ns, timer dead time
    stg.dead_eff_ns   = {stage['dead_eff_ns']!r};          // ns, effective (driver and switch delays)
    stg.shunt_settle_ns = {stage['shunt_settle_ns']!r};        // ns, shunt settled, from the low-side timer edge
    stg.duty_max_sampling = {stage['duty_max_sampling']!r};   // largest leg duty with a valid current sample
endfunction
"""
    targets = []
    if sim_dir:
        targets.append(os.path.join(sim_dir, "params"))
    for tdir in targets:
        with open(os.path.join(tdir, "stage_esc1_spice.sci"), "w", newline="\n") as f:
            f.write(sci)
        with open(os.path.join(tdir, "stage_esc1_spice.json"), "w", newline="\n") as f:
            json.dump(dict(rds_on=stage["rds_on"], r_leg=stage["r_leg"], vd=stage["vd"],
                           dead_timer_ns=stage["dead_timer_ns"],
                           dead_eff_ns=stage["dead_eff_ns"],
                           shunt_settle_ns=stage["shunt_settle_ns"],
                           duty_max_sampling=stage["duty_max_sampling"]), f, indent=2)
            f.write("\n")
        print(f"wrote {tdir}/stage_esc1_spice.sci and .json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fewer operating points")
    ap.add_argument("--export", action="store_true", help="write export/stage_esc1.json")
    ap.add_argument("--sim", default=None,
                    help="sfs-stepper-sim folder to update (default ../sfs-stepper-sim "
                         "when it exists and --export is given)")
    args = ap.parse_args()
    b = dict(BOARD)
    ok, dev = sp01(b)
    ok2, sw = sp02(b, dev, args.quick)
    ok3, sh = sp03(b, args.quick)
    ok = ok and ok2 and ok3
    if args.export:
        sim = args.sim
        if sim is None:
            cand = os.path.join(os.path.dirname(ROOT), "sfs-stepper-sim")
            sim = cand if os.path.isdir(os.path.join(cand, "params")) else None
        export(b, dev, sw, sh, sim)
    print(f"\n{'all tests passed' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
