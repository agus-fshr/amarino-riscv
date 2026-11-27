"""Reference runner: the Sail RISC-V model, configured as plain RV32I.

run(elf, workdir) runs `sail_riscv_sim --rv32 --config-override sail_rv32i.json` from
the ELF entry point until the PC reaches the program's `halt` symbol (a self-loop
every harness program ends in), and parses Sail's instruction/register/memory/
exception trace into compare.Rec records (RVFI conventions, see compare.py).

What Sail's trace gives directly: pc, instruction word, register writes, memory
accesses and exceptions. Derived here:
  rs1/rs2_rdata  from a shadow register file updated with every traced write (all
                 registers start at 0, as in Sail and in the core)
  rs1/rs2_addr   from the encoding (rv32i.reads)
  pc_wdata       the pc of the next instruction; `halt` for the last one; for a
                 trapping instruction, the address the trap handler is fetched from
  mem_*          the byte address/size Sail reports, as a word address + byte mask

The reference trace ends at the first exception: the platform has no trap handler
(mtvec resets to 0, where there is no memory), so nothing after it is meaningful.
The core never traps, so a program that traps on Sail fails co-simulation at that
instruction (field `trap`), which is the correct verdict.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from harness.common.tools import tool
from harness.gate2_cosim import rv32i
from harness.gate2_cosim.compare import Rec, MASK32

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "sail_rv32i.json"
MAX_INSNS = 2_000_000       # a harness program that runs longer than this is a harness bug

_INSN = re.compile(r"^\[(\d+)\] \[(\w+)\]: 0x([0-9A-Fa-f]+) \(0x([0-9A-Fa-f]+)\) (.*)$")
_REG = re.compile(r"^x(\d+) <- 0x([0-9A-Fa-f]+)$")
_MEM = re.compile(r"^mem\[([RWX]),0x([0-9A-Fa-f]+)\] (->|<-) 0x([0-9A-Fa-f]+)$")
_EXC = re.compile(r"^handling exc#(\S+) at priv \w+ \| tval=0x([0-9A-Fa-f]+)")
_IGNORED = (re.compile(r"^CSR \w+ \(0x[0-9A-Fa-f]+\) (<-|->) "),
            re.compile(r"^trapping from \w+ to \w+ to handle "),
            re.compile(r"^FAILURE: possible trap loop detected"),
            re.compile(r"^Unable to locate tohost symbol"),
            re.compile(r"^(Entry point|begin_signature|end_signature): "))


class SailError(RuntimeError):
    """The reference could not produce a usable trace (a harness problem, never a core verdict)."""


@dataclass
class RefTrace:
    records: list
    halt: int            # address of the halt self-loop
    ended: str           # "halt" or "trap"


def command(elf, halt, max_insns=MAX_INSNS):
    return [tool("sail_riscv_sim"), "--rv32", "--config-override", str(CONFIG),
            "--trace-instr", "--trace-reg", "--trace-mem", "--trace-exception",
            "--stop-at-pc", hex(halt), "--inst-limit", str(max_insns + 1), str(elf)]


def run(elf, halt, workdir=None, max_insns=MAX_INSNS):
    cmd = command(elf, halt, max_insns)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if workdir is not None:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        (Path(workdir) / "sail.log").write_text(r.stdout)
        (Path(workdir) / "sail.cmd").write_text(" ".join(cmd) + "\n")
    trace = parse(r.stdout, halt)
    if trace.ended == "halt":
        if r.returncode != 0:
            raise SailError(f"sail exited {r.returncode} without an exception: {r.stderr[-500:]}")
        if len(trace.records) > max_insns:
            raise SailError(f"reference did not reach halt ({halt:#x}) within {max_insns} instructions")
    return trace


def _mem_record(rec, kind, addr, value, insn):
    width = rv32i.MEM_WIDTH.get(rv32i.fields(insn)["funct3"])
    if width is None:
        raise SailError(f"memory access by non-load/store {insn:#010x}")
    if addr % width:
        raise SailError(f"misaligned {width}-byte access at {addr:#x} by {rv32i.disasm(insn)}: "
                        "gate 2 has no RVFI mapping for split accesses")
    if rec.mem_rmask or rec.mem_wmask:
        raise SailError(f"two data accesses by one instruction at {rec.pc_rdata:#x}")
    off = addr & 3
    mask = ((1 << width) - 1) << off
    data = (value << (8 * off)) & MASK32
    rec.mem_addr = addr & ~3 & MASK32
    if kind == "R":
        rec.mem_rmask, rec.mem_rdata = mask, data
    else:
        rec.mem_wmask, rec.mem_wdata = mask, data


def parse(text, halt):
    """Sail trace text -> RefTrace. Raises SailError on anything it does not understand."""
    regs = [0] * 32
    records = []
    cur = None
    trapped = False
    for n, line in enumerate(text.splitlines(), 1):
        line = line.rstrip()
        if not line:
            continue
        m = _INSN.match(line)
        if m:
            if trapped:
                break
            step, mode, pc, insn = int(m[1]), m[2], int(m[3], 16), int(m[4], 16)
            if mode != "M":
                raise SailError(f"line {n}: instruction executed in mode {mode}")
            if step != len(records):
                raise SailError(f"line {n}: step {step} after {len(records)} records")
            if cur is not None:
                cur.pc_wdata = pc
            r1, r2 = rv32i.reads(insn)
            f = rv32i.fields(insn)
            cur = Rec(order=len(records), pc_rdata=pc, insn=insn)
            if r1 and f["rs1"]:
                cur.rs1_addr, cur.rs1_rdata = f["rs1"], regs[f["rs1"]]
            if r2 and f["rs2"]:
                cur.rs2_addr, cur.rs2_rdata = f["rs2"], regs[f["rs2"]]
            records.append(cur)
            continue
        m = _REG.match(line)
        if m:
            if cur is None or trapped:
                raise SailError(f"line {n}: register write outside an instruction: {line}")
            rd, val = int(m[1]), int(m[2], 16)
            if rd == 0:
                continue
            if cur.rd_addr:
                raise SailError(f"line {n}: second register write by one instruction")
            cur.rd_addr, cur.rd_wdata = rd, val
            regs[rd] = val
            continue
        m = _MEM.match(line)
        if m:
            kind, addr, value = m[1], int(m[2], 16) & MASK32, int(m[4], 16)
            if kind == "X":
                if trapped and cur.pc_wdata is None:
                    cur.pc_wdata = addr           # first fetch from the trap handler
                continue
            if cur is None or trapped:
                raise SailError(f"line {n}: data access outside an instruction: {line}")
            _mem_record(cur, kind, addr, value, cur.insn)
            continue
        m = _EXC.match(line)
        if m:
            if cur is None:
                raise SailError(f"line {n}: exception before the first instruction")
            if not trapped:
                trapped = True
                cur.trap, cur.note = 1, m[1]
                cur.rd_addr = cur.rd_wdata = 0
                cur.mem_addr = cur.mem_rmask = cur.mem_wmask = cur.mem_rdata = cur.mem_wdata = 0
                cur.pc_wdata = None
            elif cur.pc_wdata is None and m[1].startswith("fetch-"):
                cur.pc_wdata = int(m[2], 16)      # the handler address itself faulted
            continue
        if any(p.match(line) for p in _IGNORED):
            continue
        raise SailError(f"line {n}: unrecognised Sail output: {line!r}")
    if not records:
        raise SailError("Sail executed no instructions")
    if trapped:
        if cur.pc_wdata is None:
            raise SailError("could not determine the trap handler address")
        return RefTrace(records, halt, "trap")
    cur.pc_wdata = halt
    return RefTrace(records, halt, "halt")
