# RVFI trace port

`Top` exposes a [RISC-V Formal Interface](https://github.com/YosysHQ/riscv-formal/blob/main/docs/rvfi.md) port, `top.rvfi`, with NRET=1, XLEN=32 and ILEN=32. The verification harness reads it in co-simulation and formal checks. It is produced by `amarino/rvfi.py` (`RvfiMonitor`), which only observes the pipeline and never drives anything back into it.

In the FPGA build nothing reads `rvfi`, so synthesis removes it. A yosys `equiv_make`/`equiv_induct` check shows that the FPGA netlist with the monitor is equivalent to the one without it.

## Instruction memory modes

`Top(imem="internal")` is the default and is what the FPGA build uses: the instruction memory is the initialised `Memory` inside `Fetch`, as before.

`Top(imem="external")` swaps in `ExternalImemFetch`, which exposes `top.ibus`:

| signal       | dir | meaning                                                  |
|--------------|-----|----------------------------------------------------------|
| `ibus.addr`  | out | word address, the same value that fed the internal memory |
| `ibus.rdata` | in  | the instruction word at `addr` of the **previous** cycle  |

This is the same synchronous-read latency the internal memory has, so the core sees identical timing. The Fibonacci RVFI trace is identical in both modes. The harness uses external mode so it can run arbitrary programs (co-simulation) and arbitrary instruction streams (formal) without rebuilding the core.

`Top.ports()` returns the RVFI signals, plus `ibus` in external mode, for Verilog export.

## How the core maps onto RVFI

The core is effectively **IF → ID → EX/WB**. The retire latch is combinational, so the register-file write happens at the end of the EX cycle. The core's PC counts 32-bit words, while RVFI PCs are byte addresses.

The monitor uses two kinds of signals:

- **Taps** are existing core signals copied unchanged.
- **Shadows** are small registers the core doesn't have, mainly because the latches carry decoded flags rather than the instruction word, and have no valid bit. Each shadow is clocked by the same `decode_enable/decode_mux` and `execute_enable/execute_mux` signals that drive the real latches.

| RVFI field | Source | Rule |
|---|---|---|
| `insn` | shadow ← tap `fetch.instr` | The word fetch hands the decoder, carried ID → EX alongside the real latches. |
| `pc_rdata` | shadow ← tap `fetch.pc` (registered) | The word address actually sent to instruction memory one cycle before the instruction came out, ×4. It is **not** the decode latch's `pc_out`, which the core captures one fetch later than its instruction. |
| `valid` | shadow | Set in IF when fetch passes an instruction on (`fetch.enable & ~fetch.resetn`, after the first read since reset). Cleared by `*_mux` (flush or bubble) and carried by `*_enable`. The instruction retires in the cycle it is in EX. Output is delayed by one retirement (see `pc_wdata`). |
| `pc_wdata` | shadow | The `pc_rdata` of the **next instruction that actually reaches EX**. A record is published when its successor retires, so `pc_wdata` is "where the core really went next". |
| `order` | counter | Number of records published so far. |
| `rs1_addr`, `rs2_addr` | `insn` bits | `insn[19:15]` / `insn[24:20]` for the opcodes whose encoding reads that register (rs1: OP, OP-IMM, LOAD, STORE, BRANCH, JALR; rs2: OP, STORE, BRANCH), otherwise 0. |
| `rs1_rdata`, `rs2_rdata` | tap `execute_latch.reg_data_out` | The operands the EX stage actually used, read from the register file in ID. Zero when the address is 0. |
| `rd_addr`, `rd_wdata` | tap register-file write port | `regbank.reg_addr.rd_addr` and `regbank.rd_bus.rd_data` when `regbank.we & rd_addr != 0`, the exact condition the register file writes under. Otherwise 0/0. |
| `trap`, `halt`, `intr` | constant 0 | The core implements no traps or interrupts. |
| `mode`, `ixl` | constant 3, 1 | M-mode, XLEN=32. |
| `mem_*` | constant 0 | The core has no data-memory port yet. Once it has one, these must be tapped from it. |

## What the shadows can and cannot hide

The harness is only as honest as this port, so here is what the monitor's own logic could hide:

- **`insn` and `pc_rdata` come from fetch,** not from what the decoder and latches did with it. A decoder or latch bug therefore can't make the trace report a different instruction. It shows up as wrong `rd_wdata`, wrong register reads, wrong `pc_wdata` or a missing write, which is what the insn, reg and pc checks compare against the spec.
- **`valid`, `insn` and `pc` follow the real latch controls.** If the pipeline controller flushes, holds or bubbles at the wrong time, the shadows do the same thing:
  - an instruction is lost (pc and causal checks, co-sim);
  - an instruction is retired twice (`unique`, co-sim);
  - a wrong-path instruction is retired (`pc_wdata` mismatch).

  None of these are masked. What the shadows *cannot* see is a latch whose data path ignores its own enable or mux. The effect still shows up as wrong architectural results, through the `rs*_rdata` and `rd` taps, but it is attributed to the instruction the shadows think is in EX.
- **The `rs*_addr` masking** only stops spurious reports for operands the instruction doesn't use. If the core does use such a field (for example LUI reading `rs1`), its result is wrong and the insn checks fail.
- **Register-file writes are tapped at the register file.** A write while `valid=0` (in a bubble) is not reported. It isn't masked either: the next read of that register disagrees with the reference (`reg` check, co-sim).
- **One-retirement delay.** Because `pc_wdata` is defined by the next retirement, riscv-formal's `pc_fwd` and `pc_bwd` checks hold by construction. PC correctness is carried entirely by the insn checks (each instruction's `pc_wdata` against the spec's next PC) and by co-simulation. The last instruction before the end of a run is published only when another instruction retires.

## Known consequences on the current core

These are core bugs the port reports, not things it hides:

- **Branch, JAL, JALR and AUIPC results use word arithmetic on the word PC,** and the decode latch's skewed PC, so their `pc_wdata`/`rd_wdata` disagree with RV32I.
- **Execution starts at byte address `0x4`** (word 1).
- **Loads and stores have no memory effect,** and stores write the ALU result to the register encoded in their `imm[4:0]` bits.
- **Shifts ignore the shift amount** (`alu.shamt` is unconnected).
- **Illegal instructions and `ecall`/`ebreak` don't trap.**
