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
| 2 | Co-simulation against Sail (`gate2_cosim/`). Each program runs on Sail, configured as plain RV32I M-mode (`sail_rv32i.json`, every override commented), until it reaches its `halt` self-loop; the core (Verilator) runs the same image for as many RVFI records, and the traces are compared record by record and field by field: pc, insn, trap, rs1/rs2 address and value, rd address and value, memory masks/address/data, next pc (`compare.py`). Checks: `g2.arch.<test>` (the 39 riscv-arch-test rv32i_m/I tests, unmodified, target env in `gate2_cosim/archtest_env/`), `g2.rand.<profile>.<NNN>` (random programs per profile alu/shift/upper/branch/jump/mem/full, a pure function of `--seed`, profile and index; fast mode runs indices 0-3, full 0-31), `g2.engine.*` (Amaranth simulator and Verilator give identical traces), `g2.selftest.*` (parser checked against a hand-worked program, C disabled in Sail, comparator catches a change in every field). Each co-sim check has `data={"matched", "total", "field"}`. Logs under `<out>/gate2/`; a failing check prints the command that reproduces it (`python -m harness.gate2_cosim rand alu 3`). |
| 3 | Formal verification with riscv-formal + SymbiYosys (`gate3_formal/`). `Top(imem="external")` with an unconstrained instruction word every cycle (`wrapper.sv`), bounded model checks from reset of every RV32I instruction (`g3.insn_<op>_ch0`) and of `reg`, `pc_fwd`, `pc_bwd`, `causal`, `unique`, `liveness`, `ill`, `hang` and `cover` (`checks.cfg`; depths justified in `gate3_formal/DEPTH.md`). sby runs with `--keep-going`, and every violated assertion is its own check `g3.<check>.<assertion>`, so a listed failure of one field cannot hide a new one. Counterexamples (VCD plus a disassembled retirement trace) are kept under `harness-out/<run>/gate3/`. Fast mode runs a fixed subset at full depth; `--full` (all 46 tasks) is for nightly. |

## Tools

All tools are pinned in `tools/setup_harness_tools.sh` (version and sha256 or commit)
and installed into `.tools/`. The harness uses only those copies, never tools on
`PATH`. The one exception is clang/lld for building RISC-V test programs, which
comes from the host. Python dependencies are pinned in `requirements.txt`.
Gate 2 needs `tools/setup_harness_tools.sh cad sail archtest`.

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
