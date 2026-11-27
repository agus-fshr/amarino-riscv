"""Run programs on the core under test and collect its RVFI trace.

Two engines with the same interface:
  VerilatorCore: Amaranth -> Verilog -> Verilator. Fast; used by gates 2 and 4.
  PysimCore:     Amaranth's own simulator. Slow; used to cross-check Verilator.

Both drive Top(imem="external") the same way: one reset cycle, then the instruction
bus is served from an image with one cycle of read latency (docs/rvfi.md).
"""

import hashlib
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from harness.common import design
from harness.common.tools import REPO, tool, cad_env

HERE = Path(__file__).resolve().parent

FIELDS = ("order", "insn", "pc_rdata", "pc_wdata", "rs1_addr", "rs1_rdata", "rs2_addr", "rs2_rdata",
          "rd_addr", "rd_wdata", "trap", "mem_addr", "mem_rmask", "mem_wmask", "mem_rdata", "mem_wdata")


@dataclass
class Retire:
    order: int
    insn: int
    pc_rdata: int
    pc_wdata: int
    rs1_addr: int
    rs1_rdata: int
    rs2_addr: int
    rs2_rdata: int
    rd_addr: int
    rd_wdata: int
    trap: int
    mem_addr: int
    mem_rmask: int
    mem_wmask: int
    mem_rdata: int
    mem_wdata: int
    cycle: int = 0


@dataclass
class CoreRun:
    retires: list
    cycles: int
    status: str          # "limit": reached max_retire; "timeout": hit max_cycles first


def core_source_hash():
    """Hash of the core under test (the amarino package actually imported)."""
    import amarino
    root = Path(amarino.__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


class VerilatorCore:
    """Builds once per core (cached by source hash) and runs any number of images."""
    _lock = threading.Lock()

    def __init__(self, cache_dir=REPO / "harness-out" / "cache"):
        tb = (HERE / "coresim_main.cpp").read_bytes()
        key = hashlib.sha256((core_source_hash() + tool("verilator")).encode() + tb).hexdigest()[:16]
        self.dir = Path(cache_dir) / f"coresim-{key}"
        self.exe = self.dir / "obj" / "Vtop"
        with self._lock:
            if not self.exe.exists():
                self._build()

    def _build(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        v = self.dir / "top.v"
        v.write_text(design.to_verilog(design.new_top("external")))
        cmd = [tool("verilator"), "--cc", "--exe", "--build", "-j", "2", "-O2", "-Wno-fatal", "-Wno-lint",
               "-Wno-style", "--top-module", "top", "--Mdir", str(self.dir / "obj"), "-o", "Vtop",
               str(v), str(HERE / "coresim_main.cpp")]
        r = subprocess.run(cmd, capture_output=True, text=True, env=cad_env())
        (self.dir / "build.log").write_text(r.stdout + r.stderr)
        if r.returncode != 0 or not self.exe.exists():
            raise RuntimeError(f"verilator build failed, see {self.dir / 'build.log'}")

    def run(self, words, max_retire, max_cycles, workdir):
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        img = workdir / "image.hex"
        img.write_text("".join(f"{w & 0xFFFFFFFF:08x}\n" for w in words))
        r = subprocess.run([str(self.exe), str(img), str(max_retire), str(max_cycles)],
                           capture_output=True, text=True)
        (workdir / "core.trace").write_text(r.stdout)
        if r.returncode != 0:
            raise RuntimeError(f"core simulation exited {r.returncode}: {r.stderr[-500:]}")
        return parse_trace(r.stdout)


def parse_trace(text):
    retires, end = [], None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "R":
            vals = parts[1:]
            rec = {}
            for name, v in zip(FIELDS, vals[:-1]):
                rec[name] = int(v, 10) if name in ("order", "rs1_addr", "rs2_addr", "rd_addr") else int(v, 16)
            rec["cycle"] = int(vals[-1])
            retires.append(Retire(**rec))
        elif parts[0] == "END":
            end = (int(parts[2]), parts[3])
    if end is None:
        raise RuntimeError("core simulation output has no END line")
    return CoreRun(retires, end[0], end[1])


class PysimCore:
    """Amaranth simulator with the same protocol as coresim_main.cpp."""

    def run(self, words, max_retire, max_cycles, workdir=None):
        from amaranth import ClockDomain, Elaboratable, Module
        from amaranth.hdl import Fragment
        from amaranth.sim import Simulator

        top = design.new_top("external")
        cd = ClockDomain("sync")

        class Bench(Elaboratable):
            # Declare `sync` here so its reset can be driven; Verilog export gets the
            # same domain implicitly as the top-level clk/rst ports.
            def elaborate(self, platform):
                m = Module()
                m.domains.sync = cd
                m.submodules.top = top
                return m

        frag = Fragment.get(Bench(), design.StubPlatform())
        rst = cd.rst
        sim = Simulator(frag)
        sim.add_clock(1e-8)
        out = {"retires": [], "cycles": 0}

        async def bench(ctx):
            ctx.set(top.ibus.rdata, 0)
            ctx.set(rst, 1)                      # one reset cycle, as in coresim_main.cpp
            await ctx.tick()
            ctx.set(rst, 0)
            cycle = 0
            while cycle < max_cycles and len(out["retires"]) < max_retire:
                if ctx.get(top.rvfi.valid):
                    rec = {name: ctx.get(getattr(top.rvfi, name)) for name in FIELDS}
                    out["retires"].append(Retire(**rec, cycle=cycle))
                addr = ctx.get(top.ibus.addr)
                await ctx.tick()
                ctx.set(top.ibus.rdata, words[addr] if addr < len(words) else 0)
                cycle += 1
            out["cycles"] = cycle

        sim.add_testbench(bench)
        sim.run()
        status = "limit" if len(out["retires"]) >= max_retire else "timeout"
        return CoreRun(out["retires"], out["cycles"], status)

