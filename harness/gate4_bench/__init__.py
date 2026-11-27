"""Gate 4: benchmarks and synthesis (report-only).

Per benchmark (fast mode: fib; full mode: fib, dhrystone, coremark):

  g4.build.<bench>   the benchmark builds and the reference simulator (Sail) runs it
                     to `_halt` with the expected result in a0. This proves the
                     benchmark setup itself is valid, independent of the core, so it is
                     a normal (gating) check.
  g4.bench.<bench>   INFO. The core runs the same image for the reference's
                     instruction count (+1 for the `_halt` instruction itself). Reports
                     whether it completed (retired every instruction the reference did,
                     in order, with the same pc/insn/rd write/next pc, ending at
                     `_halt`), retired instructions, cycles, CPI and the first
                     retirement where its trace diverges from Sail's.

Synthesis (both modes; yosys takes about 10 s):

  g4.synth.yosys     INFO. yosys `synth_xilinx -flatten` of Top(imem="internal") with
                     only the FPGA build's ports (clk, rst, UART tx). An estimate of
                     resources; no timing. RVFI is removed because nothing reads it.
  g4.synth.vivado    INFO. Out-of-context synth + place + route for the Arty A7-100
                     part at 100 MHz, if $VIVADO points to a Vivado install
                     (the `vivado` binary or its install directory); otherwise reports
                     it as unavailable.

The report is also written as markdown to <out>/gate4/report.md and printed.
"""

import json
import os
import re
import shutil
import subprocess
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from harness.common import program
from harness.common.coresim import FIELDS, VerilatorCore
from harness.common.tools import TOOLS, ToolMissing, cad_env, tool
from harness.runner import Check, guarded

GATE = 4
NAME = "benchmarks + synthesis (report-only)"
HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"

SAIL_INST_LIMIT = 3_000_000       # every benchmark finishes well inside this
VIVADO_PART = "xc7a100tcsg324-1"  # Arty A7-100
VIVADO_PERIOD_NS = 10.0           # 100 MHz


# ---- benchmark definitions -----------------------------------------------------

def _dir(name):
    path = TOOLS / name
    if not (path / ".pinned").exists():
        raise ToolMissing(f"{name} sources not found at {path}; run tools/setup_harness_tools.sh bench")
    return path


@dataclass
class Bench:
    name: str
    expect: int                      # a0 at _halt
    fast: bool

    def sources_and_flags(self):
        inc = [f"-I{RUNTIME / 'include'}"]
        rt = [RUNTIME / "crt0.S", RUNTIME / "libc.c"]
        if self.name == "fib":
            return rt + [HERE / "fib" / "fib.c"], ["-O2", *inc]
        if self.name == "dhrystone":
            d = _dir("riscv-tests") / "benchmarks" / "dhrystone"
            return (rt + [HERE / "dhrystone" / "check.c", d / "dhrystone.c", d / "dhrystone_main.c"],
                    ["-O2", "-std=gnu89", "-w", "-Dmain=dhrystone_main", *inc,
                     f"-I{HERE / 'dhrystone'}", f"-I{d}"])
        if self.name == "coremark":
            c = _dir("coremark")
            files = ["core_list_join.c", "core_main.c", "core_matrix.c", "core_state.c", "core_util.c"]
            return (rt + [HERE / "coremark" / "core_portme.c"] + [c / f for f in files],
                    ["-O2", "-DITERATIONS=1", "-DPERFORMANCE_RUN=1", "-Dmain=coremark_main", *inc,
                     f"-I{HERE / 'coremark'}", f"-I{c}"])
        raise KeyError(self.name)


BENCHES = [
    Bench("fib", 75025, fast=True),          # fib(25) iterative (and fib(15) recursive)
    Bench("dhrystone", 0xD4, fast=False),    # 500 runs, final values checked
    Bench("coremark", 0xC0DE, fast=False),   # 1 iteration, 2K performance seeds, CRCs checked
]


# ---- reference (Sail) ----------------------------------------------------------

@dataclass
class Reference:
    pc: array          # per retirement, including the final `_halt` entry
    insn: array
    rd: array          # 0: no register write
    rd_val: array
    a0: int
    count: int         # instructions before _halt (Sail's instruction count)
    reached_halt: bool


_INSN = re.compile(r"\[(\d+)\] \[M\]: 0x([0-9A-Fa-f]+) \(0x([0-9A-Fa-f]+)\)")
_REG = re.compile(r"x(\d+) <- 0x([0-9A-Fa-f]+)")


def run_reference(elf, halt, workdir):
    """Run Sail until the PC reaches `halt`, streaming its instruction/register trace."""
    cmd = [tool("sail_riscv_sim"), "--rv32", "--stop-at-pc", hex(halt), "--inst-limit", str(SAIL_INST_LIMIT),
           "--trace-instr", "--trace-reg", "--trace-output", "/dev/stdout", str(elf)]
    pc, insn, rd, rd_val = array("I"), array("I"), array("B"), array("I")
    regs = [0] * 32
    other = []
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
        for line in p.stdout:
            m = _INSN.match(line)
            if m:
                pc.append(int(m.group(2), 16))
                insn.append(int(m.group(3), 16))
                rd.append(0)
                rd_val.append(0)
                continue
            m = _REG.match(line)
            if m and pc:
                r, v = int(m.group(1)), int(m.group(2), 16)
                if r:
                    rd[-1], rd_val[-1], regs[r] = r, v, v
                continue
            if len(other) < 200:
                other.append(line)
    (Path(workdir) / "sail.log").write_text(" ".join(cmd) + "\n" + "".join(other))
    if p.returncode != 0:
        raise RuntimeError(f"sail_riscv_sim exited {p.returncode}; see {workdir}/sail.log")
    count = len(pc)
    # crt0 falls through from its final store into _halt, so stopping at _halt (rather
    # than hitting the instruction limit) means the last instruction is the one before it.
    reached = count < SAIL_INST_LIMIT and count > 0 and pc[-1] + 4 == halt
    pc.append(halt)
    insn.append(0x0000006F)          # _halt: j _halt
    rd.append(0)
    rd_val.append(0)
    return Reference(pc, insn, rd, rd_val, regs[10], count, reached)


def build_check(bench, ctx):
    work = ctx.path("gate4", bench.name, "x").parent
    sources, flags = bench.sources_and_flags()
    elf = program.build(sources, work / f"{bench.name}.elf", flags)
    image = program.load_elf(elf)
    halt = image.symbols["_halt"]
    ref = run_reference(elf, halt, work)
    ok = ref.reached_halt and ref.a0 == bench.expect
    detail = (f"Sail: {ref.count} instructions to _halt ({halt:#x}), a0={ref.a0:#x} (expected {bench.expect:#x})"
              if ref.reached_halt else
              f"Sail did not reach _halt ({halt:#x}) within {SAIL_INST_LIMIT} instructions; see {work}/sail.log")
    return Check(f"g4.build.{bench.name}", ok, detail,
                 data={"reference_retired": ref.count, "a0": ref.a0}), image, ref


# ---- core run --------------------------------------------------------------------

def _describe(i, ref, what):
    return f"diverged at retirement {i} (pc {ref.pc[i]:#010x}, insn {ref.insn[i]:#010x}): {what}"


def run_core(core, image, ref, workdir):
    """Stream the core's RVFI trace and compare it with the reference as it arrives."""
    workdir = Path(workdir)
    img = workdir / "image.hex"
    img.write_text("".join(f"{w & 0xFFFFFFFF:08x}\n" for w in image.words))
    max_retire = ref.count + 1                 # +1: the retirement of `_halt` itself
    max_cycles = 50 * max_retire + 1000
    base = program.IMAGE_BASE
    n = 0
    diverged = None
    halt_cycle = None
    end = None
    head = []
    with subprocess.Popen([str(core.exe), str(img), str(max_retire), str(max_cycles)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as p:
        for line in p.stdout:
            if line.startswith("R "):
                if len(head) < 2000:
                    head.append(line)
                v = line.split()[1:]
                rec = dict(zip(FIELDS, v[:-1]))
                if diverged is None:
                    i = n
                    cpc = (int(rec["pc_rdata"], 16) + base) & 0xFFFFFFFF
                    cnext = (int(rec["pc_wdata"], 16) + base) & 0xFFFFFFFF
                    cinsn = int(rec["insn"], 16)
                    crd, cval = int(rec["rd_addr"]), int(rec["rd_wdata"], 16)
                    rnext = ref.pc[i + 1] if i + 1 < len(ref.pc) else ref.pc[i]
                    if cpc != ref.pc[i]:
                        diverged = (i, _describe(i, ref, f"core retired pc {cpc:#010x}"))
                    elif cinsn != ref.insn[i]:
                        diverged = (i, _describe(i, ref, f"core retired insn {cinsn:#010x}"))
                    elif (crd, cval if crd else 0) != (ref.rd[i], ref.rd_val[i]):
                        c = f"x{crd}={cval:#x}" if crd else "no write"
                        r = f"x{ref.rd[i]}={ref.rd_val[i]:#x}" if ref.rd[i] else "no write"
                        diverged = (i, _describe(i, ref, f"core wrote {c}, reference {r}"))
                    elif cnext != rnext:
                        diverged = (i, _describe(i, ref, f"core went to {cnext:#010x}, reference to {rnext:#010x}"))
                if n == ref.count:
                    halt_cycle = int(v[-1])
                n += 1
            elif line.startswith("END"):
                end = line.split()
        err = p.stderr.read()
    (workdir / "core.trace.head").write_text("".join(head))
    if p.returncode != 0 or end is None:
        raise RuntimeError(f"core simulation exited {p.returncode}: {err[-500:]}")
    cycles, status = int(end[2]), end[3]
    completed = diverged is None and n >= max_retire
    if completed:
        retired, cycles = ref.count, halt_cycle     # instructions before _halt, cycles until it retires
    else:
        retired = n                                 # everything the core retired in the run
    return {
        "completed": completed,
        "reference_retired": ref.count,
        "retired": retired,
        "cycles": cycles,
        "cpi": round(cycles / retired, 3) if retired else None,
        "diverged_at": diverged[0] if diverged else None,
        "divergence": diverged[1] if diverged else None,
        "end": status,                         # "limit" or "timeout" (max_cycles reached)
        "max_cycles": max_cycles,
    }


def bench_check(bench, ctx, core):
    build, image, ref = build_check(bench, ctx)
    if not build.ok:
        return [build, Check(f"g4.bench.{bench.name}", False, "not run: the benchmark itself is broken "
                             "(see g4.build)", info=True)]
    if isinstance(core, Exception):
        return [build, Check(f"g4.bench.{bench.name}", False, f"core did not build: {core}", info=True)]
    d = run_core(core, image, ref, ctx.path("gate4", bench.name, "x").parent)
    if d["completed"]:
        detail = (f"completed: {d['retired']} instructions in {d['cycles']} cycles, CPI {d['cpi']} "
                  f"(Sail: {ref.count})")
    else:
        how = ("timed out" if d["end"] == "timeout" else "hit the retirement limit") + \
              f" after {d['retired']} retirements / {d['cycles']} cycles"
        detail = f"did not complete: {d['divergence'] or 'no divergence seen'}; {how} (Sail: {ref.count})"
    return [build, Check(f"g4.bench.{bench.name}", d["completed"], detail, info=True, data=d)]


# ---- synthesis ---------------------------------------------------------------------

def _fpga_netlist():
    """Top(imem="internal") as the FPGA build sees it: only the platform pins are ports
    (plus the sync domain's clk/rst), so the unused RVFI port is optimised away."""
    from amaranth.back import rtlil
    from harness.common import design
    top = design.new_top("internal")
    frag, _, platform = design.elaborate(top)
    return rtlil.convert(frag, name="top", ports=list(platform.pins.values()))


_RESOURCE_GROUPS = {
    "LUT": re.compile(r"LUT\d$"),
    "FF": re.compile(r"FD[RSCP]E$"),
    "BRAM": re.compile(r"RAMB(18|36)E1$"),
    "LUTRAM": re.compile(r"RAM\d+[XM]"),
    "CARRY": re.compile(r"CARRY4$"),
    "DSP": re.compile(r"DSP48E1$"),
    "MUXF": re.compile(r"MUXF[78]$"),
}


def yosys_check(ctx):
    work = ctx.path("gate4", "synth", "x").parent
    il = work / "top.il"
    try:
        il.write_text(_fpga_netlist())
    except Exception as e:  # noqa: BLE001 - a core that does not elaborate is a result here
        return Check("g4.synth.yosys", False, f"Top(imem='internal') did not elaborate: {e!r}", info=True)
    log, stat = work / "yosys.log", work / "stat.json"
    script = f"read_rtlil {il}; synth_xilinx -flatten -top top; tee -q -o {stat} stat -json"
    r = subprocess.run([tool("yosys"), "-q", "-l", str(log), "-p", script],
                       capture_output=True, text=True, env=cad_env())
    if r.returncode != 0:
        return Check("g4.synth.yosys", False, f"yosys synth_xilinx failed; log: {log}\n{r.stderr[-1500:]}",
                     info=True)
    cells = json.loads(stat.read_text())["modules"]["\\top"]["num_cells_by_type"]
    data = {k: sum(n for c, n in cells.items() if rx.match(c)) for k, rx in _RESOURCE_GROUPS.items()}
    # RAMB36E1 counts as two 18K halves.
    data["BRAM18"] = cells.get("RAMB18E1", 0) + 2 * cells.get("RAMB36E1", 0)
    data["cells"] = {c: n for c, n in cells.items() if not c.startswith("$")}
    detail = ("yosys estimate (no timing): " + ", ".join(f"{k} {data[k]}" for k in
              ("LUT", "FF", "BRAM18", "LUTRAM", "CARRY", "DSP")) + f"; log: {log}")
    return Check("g4.synth.yosys", True, detail, info=True, data=data)


def _vivado_bin():
    v = os.environ.get("VIVADO")
    if not v:
        return None
    p = Path(v)
    if p.is_dir():
        p = p / "bin" / "vivado"
    return p if p.exists() else None


_VIVADO_TCL = """\
read_verilog {v}
synth_design -top top -part {part} -mode out_of_context
create_clock -period {period} -name clk [get_ports clk]
opt_design
place_design
route_design
set path [get_timing_paths -max_paths 1 -nworst 1 -setup]
set f [open {wns_file} w]
puts $f [get_property SLACK $path]
close $f
report_utilization -file {util}
report_timing_summary -file {timing}
"""


def vivado_check(ctx):
    exe = _vivado_bin()
    if exe is None:
        why = ("$VIVADO is not set" if not os.environ.get("VIVADO")
               else f"$VIVADO={os.environ['VIVADO']} has no vivado binary")
        return Check("g4.synth.vivado", None, f"unavailable: Vivado not installed ({why})", info=True,
                     data={"available": False})
    from harness.common import design
    work = ctx.path("gate4", "vivado", "x").parent
    v = work / "top.v"
    top = design.new_top("internal")
    frag, _, platform = design.elaborate(top)
    from amaranth.back import verilog
    v.write_text(verilog.convert(frag, name="top", ports=list(platform.pins.values())))
    wns_file, util, timing = work / "wns.txt", work / "utilization.rpt", work / "timing.rpt"
    tcl = work / "run.tcl"
    tcl.write_text(_VIVADO_TCL.format(v=v, part=VIVADO_PART, period=VIVADO_PERIOD_NS,
                                      wns_file=wns_file, util=util, timing=timing))
    r = subprocess.run([str(exe), "-mode", "batch", "-nojournal", "-nolog", "-source", str(tcl)],
                       capture_output=True, text=True, cwd=work)
    (work / "vivado.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0 or not wns_file.exists():
        return Check("g4.synth.vivado", False, f"Vivado run failed; log: {work / 'vivado.log'}", info=True)
    wns = float(wns_file.read_text().split()[0])
    data = {"available": True, "part": VIVADO_PART, "period_ns": VIVADO_PERIOD_NS, "wns_ns": wns,
            "fmax_mhz": round(1000.0 / (VIVADO_PERIOD_NS - wns), 1)}
    for key, label in (("LUT", "Slice LUTs"), ("FF", "Slice Registers"),
                       ("BRAM_tiles", "Block RAM Tile"), ("DSP", "DSPs")):
        m = re.search(rf"\|\s*{re.escape(label)}\*?\s*\|\s*([\d.]+)", util.read_text())
        if m:
            data[key] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
    detail = (f"Vivado {VIVADO_PART} OOC @ {1000 / VIVADO_PERIOD_NS:.0f} MHz: WNS {wns} ns -> "
              f"Fmax {data['fmax_mhz']} MHz; " + ", ".join(f"{k} {data[k]}" for k in
                                                         ("LUT", "FF", "BRAM_tiles", "DSP") if k in data))
    return Check("g4.synth.vivado", True, detail, info=True, data=data)


# ---- report -----------------------------------------------------------------------

def _fmt(x):
    return "—" if x is None else str(x)


def report(checks, mode):
    by = {c.id: c for c in checks}
    lines = [f"# Gate 4 report ({mode} mode)", "",
             "Report-only. Reference counts are from Sail (`sail_riscv_sim --rv32`, until `_halt`); "
             "the core runs the same image under Verilator for that many retirements (+1 for `_halt`) "
             "and at most 50x+1000 cycles.", "",
             "| benchmark | reference retired (Sail) | core | retired | cycles | CPI | first divergence |",
             "|---|---|---|---|---|---|---|"]
    for b in BENCHES:
        build, c = by.get(f"g4.build.{b.name}"), by.get(f"g4.bench.{b.name}")
        if build is None:
            continue
        if c is None or not c.data:
            why = "benchmark broken" if build.ok is False else (c.detail.splitlines()[0] if c else "not run")
            lines.append(f"| {b.name} | {_fmt(build.data.get('reference_retired'))} | {why} | — | — | — | — |")
            continue
        d = c.data
        status = "completed" if d["completed"] else (
            "did not complete (timed out)" if d["end"] == "timeout" else "did not complete")
        div = d["divergence"].removeprefix("diverged at ") if d["divergence"] else "—"
        lines.append(f"| {b.name} | {d['reference_retired']} | {status} | {d['retired']} | {d['cycles']} | "
                     f"{_fmt(d['cpi'])} | {div} |")
    lines += ["", "CPI is cycles/retired over what the core actually ran; it only measures the "
              "benchmark when the core completed.", ""]
    y = by.get("g4.synth.yosys")
    lines += ["## Synthesis", "", "| tool | result |", "|---|---|"]
    if y is not None:
        if y.data:
            res = ", ".join(f"{k} {y.data[k]}" for k in ("LUT", "FF", "BRAM18", "LUTRAM", "CARRY", "DSP", "MUXF"))
            lines.append(f"| yosys `synth_xilinx -flatten` (estimate, no timing) | {res} |")
        else:
            lines.append(f"| yosys | {y.detail.splitlines()[0]} |")
    v = by.get("g4.synth.vivado")
    if v is not None:
        lines.append(f"| Vivado ({VIVADO_PART}, 100 MHz, OOC) | {v.detail.splitlines()[0]} |")
    return "\n".join(lines) + "\n"


# ---- gate entry point -------------------------------------------------------------

def run(ctx):
    benches = [b for b in BENCHES if ctx.mode == "full" or b.fast]
    try:
        core = VerilatorCore()
    except ToolMissing:
        raise
    except Exception as e:  # noqa: BLE001 - a core that does not build is a report-only result
        core = e

    def one(bench):
        out = guarded(f"g4.build.{bench.name}", bench_check, bench, ctx, core)
        return out if isinstance(out, list) else [out]

    # At most two heavy processes at a time (Sail and Verilator runs).
    with ThreadPoolExecutor(max_workers=max(1, min(2, ctx.jobs))) as pool:
        futs = [pool.submit(one, b) for b in benches]
        synth = pool.submit(guarded, "g4.synth.yosys", yosys_check, ctx)
        checks = [c for f in futs for c in f.result()] + [synth.result()]
    checks.append(guarded("g4.synth.vivado", vivado_check, ctx))

    text = report(checks, ctx.mode)
    ctx.path("gate4", "report.md").write_text(text)
    print(text)
    return checks
