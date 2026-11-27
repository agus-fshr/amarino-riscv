# Gate 3 depths

| check | `[depth]` line | meaning |
|---|---|---|
| `insn_*`, `ill` | `20` | the checked instruction retires at step 20 |
| `pc_fwd`, `pc_bwd`, `causal` | `1 20` | reset for 1 cycle, check at step 20 |
| `unique` | `1 10 20` | instruction marked at step 10, duplicates searched for up to step 20 |
| `liveness` | `1 10 30` | instruction retiring at step 10 must have its successor by step 30 |
| `reg` | `1 25` | check at step 25 |
| `hang`, `cover` | `1 20` | something must retire / the cover goals must be reachable by step 20 |

Step 0 is the reset cycle (`rvfi_testbench` asserts reset only while `$initstate`). The
first number of each line, the checkers' reset length, is 1. That is the minimum, so the
checkers see every retirement after the core's own reset. A larger value would only
hide early retirements from `reg`, `causal` and the pc checks.

## The pipeline

The core is effectively 3 stages: **IF → ID → EX/WB**. The register file is written at the
end of the EX cycle. The instruction-memory read is synchronous, so a word requested
in cycle *t* arrives in *t+1*. The RVFI monitor publishes a record one retirement late:
record *n* appears when instruction *n+1* reaches EX, because that is the point where
its `pc_wdata` is known (see `docs/rvfi.md`). This delay is included in every number
below.

## Measured on this core

`python -m harness.gate3_formal.measure_depth` measured these. It reuses the generated
riscv-formal tasks, with the same wrapper, the same unconstrained instruction stream and
the same solver, so each number is a **proof over all instruction streams**, not a
simulation:

| quantity | value | how |
|---|---|---|
| earliest step of the first record | **5** | cover `cnt_insns == 1`, no skip |
| earliest step of the n-th record | **n + 4**: one record per cycle at best, so 16 by step 20 | cover `cnt_insns == n` |
| latest step of the first record | **9** | `hang` check moved to step c: fails for c ≤ 9, passes for c = 10 |
| longest gap between consecutive records | **3 cycles** | `liveness` with the trigger at step t = 5..12 and a window of g cycles: fails for g = 2, passes for g = 3, for every t |
| records that can exist at step 20 | **4 to 16** (every value in between is reachable) | cover `check && cnt_insns == n` at step 20 |

A 3-cycle gap is what a taken jump or branch costs here: the redirect happens late and two
fetched words are discarded. The first record can be as late as step 9 when the first
fetched words are flushed. An example witness (`cnt_insns == 4` at step 20) is a chain of
branches with a record every 3 cycles: steps 9, 12, 15, 18.

## Why these depths are enough

* **Non-vacuity.** Every insn and consistency check *assumes* a retirement at its check
  step. sby runs smtbmc with `--presat`, so a check whose assumptions cannot be met ends in
  `ERROR`, never in `PASS`. This was confirmed by moving `insn_add` to step 3: the result
  is "Assumptions are unsatisfiable!" and `DONE (ERROR)`. The gate reports that as ERROR.
  Step 20 is well past the latest first record (9), and `g3.cover` proves on every run that
  a retirement at step 20 is reachable.
* **Pipeline history.** In an IF/ID/EX-WB pipeline, an instruction in EX can interact only
  with the instructions in flight with it: at most one older instruction just written
  back, plus the younger ones in ID and IF that a flush or stall acts on. Hazard,
  forwarding and flush bugs therefore need about 2–3 instructions of context. At step 20
  the checked instruction can have anywhere from 3 to 15 retired predecessors, and
  `g3.cover` asserts that both ≥3 and ≥7 predecessors are reachable. The solver also
  controls every discarded wrong-path word, since the instruction bus is free every
  cycle.
* **Operand values.** The insn checks take the operands from `rs1_rdata`/`rs2_rdata`, but
  the core reads them from a register file that starts at zero. Giving two registers
  arbitrary 32-bit values takes 4 instructions (`lui`+`addi` each), which fits easily into
  the 15 possible predecessors.
* **reg (25).** This check needs a write to a register, any number of instructions, then a
  read of it. 25 steps allow up to 21 records, and the current core already fails it with
  a 2-instruction pattern.
* **liveness (trigger 10, check 30).** The successor must appear within 20 cycles. The
  proven maximum gap is 3, so the margin is more than 6×. The trigger at 10 is after the
  latest first record (9).
* **unique (trigger 10, check 20).** The record at step 10 is compared against every
  record from step 1 to 20, which spans ≥ 4 records on each side.
* **State the window cannot reach.** The UART's baud counter (868 cycles per bit) and its
  frame shift register cannot reach most of their states in 20–30 cycles. The pipeline
  never reads them back: `uart.ready` only gates `uart.start`, and nothing stalls on it.
  So they cannot affect RVFI, and no bug that depends on them can exist today. If the core
  ever stalls on the UART or on a future data bus, the depth must cover that latency, or a
  stall input must be added to the wrapper. Re-measure in that case.

## Cost

With the boolector solver, every check takes 2–14 s and less than 150 MB RSS. Full mode
(46 tasks) takes about 2 minutes with 2 jobs, and fast mode about 25 s. The depths are
not limited by cost. They could be raised a lot if a future, longer-latency pipeline
needs it.

## Solver

The solver is boolector, riscv-formal's default. On `insn_add` (depth 20, keep-going) it
took 6 s, against 4 s for yices and 50 s for bitwuzla. yices is about as fast. We kept the
default so the setup matches riscv-formal's own examples.

## When to revisit

Rerun `measure_depth` whenever the pipeline changes: a new stage, a data-memory port,
stalls or a new reset PC. Then update this file. Two things mean the depths must grow:
if `g3.cover` or `g3.hang` start failing, or if the measured maximum gap gets close to
the liveness window.
