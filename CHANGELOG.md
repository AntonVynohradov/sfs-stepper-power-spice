# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- STL180N6F7 model fitted to the datasheet (`models/stl180n6f7_sfs.lib`, ngspice VDMOS):
  Rds(on), body diode, Ciss / Coss / Crss, gate charge.
- L6387E behavioural driver (`models/l6387_behav.lib`): interlocking, propagation delay,
  source / sink output stages, bootstrap DMOS.
- One B-G431B-ESC1 leg (`circuits/esc1_leg.lib`): driver, two MOSFETs, gate resistors,
  bootstrap, low-side shunt, layout inductances.
- `tools/run_tests.py`: tests SP01 (device against the datasheet), SP02 (dead time
  voltage error, dead time margin, gate bump), SP03 (low-side shunt sample and
  settling); `--export` writes `export/stage_esc1.json` and the stage parameters of
  `sfs-stepper-sim` (`params/stage_esc1_spice.sci` / `.json`).
- `tools/plot_leg.py`: switch node at both edges (`docs/leg_edges.png`).
