#!/usr/bin/env python3
################################################################################
# sfs-stepper-power-spice: power stage models (ngspice)
#
# Description: Plots the switch node of one ESC1 leg around both edges for a phase
#              current of +2 A and -2 A (docs/leg_edges.png).
#
# Author:      Anton Vynohradov
# Email:       avynohradov@systemfromscratch.com
# License:     Apache License 2.0
# Copyright:   (c) 2026 Anton Vynohradov
#
# Usage:       python tools/plot_leg.py
#
# SPDX-License-Identifier: Apache-2.0
#
################################################################################

import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_tests as rt  # noqa: E402

INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
SERIES = {2.0: "#2a78d6", -2.0: "#eb6834"}   # categorical slots 1 and 2


def main():
    b = dict(rt.BOARD)
    ts = 1.0 / b["fs"]
    d = 0.5
    td = b["dead_ns"] * 1e-9
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    fig.patch.set_facecolor("#fcfcfb")
    for i in (2.0, -2.0):
        data = rt.leg_bench(f"plot_i{i:+.1f}", b, d, i, 6, tmax="1n")
        t, v = data[:, 0], data[:, 1]
        t0 = t[-1] - ts
        for ax, edge in zip(axes, (ts / 2 * (1 - d), ts / 2 * (1 + d))):
            sel = (t >= t0 + edge - 0.3e-6) & (t <= t0 + edge + 1.2e-6)
            ax.plot((t[sel] - t0 - edge) * 1e9, v[sel], color=SERIES[i], lw=2,
                    label=f"i = {i:+.0f} A")
    titles = ("High side on (timer edge at 0, high side gate after the dead time)",
              "High side off (timer edge at 0, low side gate after the dead time)")
    for ax, title in zip(axes, titles):
        ax.set_facecolor("#fcfcfb")
        ax.axvline(0, color=INK2, lw=1, ls=":")
        ax.axvline(td * 1e9, color=INK2, lw=1, ls="--")
        ax.set_title(title, fontsize=9, color=INK)
        ax.set_xlabel("time from the ideal edge [ns]", color=INK2)
        ax.grid(color=GRID, lw=0.8)
        ax.tick_params(colors=INK2)
        for sp in ax.spines.values():
            sp.set_color(GRID)
    axes[0].set_ylabel("switch node [V]", color=INK2)
    axes[0].legend(frameon=False, labelcolor=INK)
    fig.suptitle(f"B-G431B-ESC1 leg, {b['vbus']:.0f} V, timer dead time {b['dead_ns']:.0f} ns "
                 f"(dashed), Rg {b['rg']:.0f} Ohm", color=INK, fontsize=10)
    fig.tight_layout()
    os.makedirs(os.path.join(rt.ROOT, "docs"), exist_ok=True)
    path = os.path.join(rt.ROOT, "docs", "leg_edges.png")
    fig.savefig(path, dpi=120)
    print(f"wrote {os.path.relpath(path, rt.ROOT)}")


if __name__ == "__main__":
    main()
