# Correctness harness

The harness is the only judge of whether the core in `amarino/` is correct.
"All gates green" means every check passed, or failed in a way that is listed,
with a reason, in `spec/expected_failures.toml`.

```bash
tools/setup_harness_tools.sh      # once: pinned tools into .tools/ (about 4 GB)
python -m harness                 # fast mode: the edit/test loop
python -m harness --full          # everything; this is the judge
python -m harness --gate 1 -v     # one gate, listing passing checks too
python -m harness.selftest        # the runner's own verdict logic
```

Logs and generated netlists go to `harness-out/<timestamp>/`. `--json FILE` writes
machine-readable results, and `--core-dir DIR` tests `DIR/amarino` instead of the
repo's core (used by the mutation suite).

## Verdicts

| verdict | meaning | red? |
|---|---|---|
| PASS  | passed | no |
| XFAIL | failed, and listed in `spec/expected_failures.toml` | no |
| FAIL  | failed, not listed | **yes** |
| XPASS | passed, but listed: the bug was fixed, so remove the entry | **yes** |
| ERROR | could not run (tool missing, harness crash); never satisfies a listed failure | **yes** |
| STALE | listed, but no check with that id exists any more | **yes** |
| INFO  | report-only (benchmarks) | no |

Each listed entry names exactly one check id; there are no wildcards. Checks are
deliberately fine-grained, so one listed failure cannot hide a new failure of a
different kind.

## Gates

| gate | what it checks |
|---|---|
| 1 | Elaboration and lint. For Top (both instruction-memory modes) and every module class in its hierarchy: elaboration without exceptions or Python warnings, conversion to a netlist (catches combinational cycles and driver conflicts), yosys `check`, and `verilator --lint-only -Wall` (codegen-only codes waived in `gate1_lint/verilator_waivers.toml`, each with a reason). Also checks that every submodule input port is driven by Top, and that only Top declares clock domains. |

## Tools

All tools are pinned in `tools/setup_harness_tools.sh` (version and sha256 or commit)
and installed into `.tools/`. The harness uses only those copies, never tools on
`PATH`. The one exception is clang/lld for building RISC-V test programs, which
comes from the host. Python dependencies are pinned in `requirements.txt`.

| tool | version |
|---|---|
| YosysHQ oss-cad-suite (yosys, sby, verilator, yices, boolector, bitwuzla) | 2026-11-24 |
| sail-riscv (`sail_riscv_sim`) | 0.16 |
| riscv-formal | c992aa6 |
| riscv-arch-test | 3.9.1 |
| Amaranth | 0.5.10 |

## The stub platform

`UART32` requests its pin from the platform unconditionally, so elaborating Top
outside a board build needs a platform. `common/design.py` provides `StubPlatform`,
which hands out a plain signal for `uart#0` and refuses any other resource. It does
not change what the core does.
