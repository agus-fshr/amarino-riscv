"""Gate 2: co-simulation against the Sail RISC-V reference model.

Each program runs on Sail (plain RV32I, sail_rv32i.json) until it reaches its `halt`
self-loop, giving a reference trace of L retirements. The core runs the same image
(Verilator) until it has published L RVFI records or exhausted a generous cycle budget,
and the two traces are compared record by record and field by field (compare.py).
Every co-simulation check carries data={"matched", "total", "field"}: the number of
identical leading records, L, and the first mismatching field (None if identical).

  g2.arch.<test>            riscv-arch-test rv32i_m/I, built unmodified for this target
                            (archtest_env/model_test.h). Fast and full mode.
  g2.rand.<profile>.<NNN>   random programs (randgen.py) from (seed, profile, NNN).
                            Fast mode: indices 0..RAND_FAST-1; full: 0..RAND_FULL-1.
  g2.engine.<program>       the core's RVFI trace is identical under Amaranth's simulator
                            and Verilator (so the fast engine can be trusted)
  g2.selftest.*             the gate's own parser/comparator/generator (selftests.py)

Logs: <out>/gate2/<kind>/<name>/ holds the program (.S/.elf), sail.log, core.trace and
compare.log (the divergence with context). Each failing check prints the command that
reproduces it.
"""

from pathlib import Path

from harness.common.coresim import PysimCore
from harness.gate2_cosim import cosim, programs, randgen, selftests, sail
from harness.runner import Check, guarded, parallel

GATE = 2
NAME = "co-simulation (Sail vs core)"

# All 39 arch tests take a few seconds, so fast mode runs them all; fast mode only
# runs fewer random programs (the same programs as the first RAND_FAST of full mode).
RAND_FAST = 4
RAND_FULL = 32
ENGINE_PROGRAMS = (("arch", "fence-01"), ("rand", "full", 0))


def _repro(ctx, *what):
    import amarino
    core_dir = Path(amarino.__file__).resolve().parent.parent
    repo = Path(__file__).resolve().parents[2]
    cmd = f"python -m harness.gate2_cosim {' '.join(map(str, what))} --seed {ctx.seed:#x}"
    if core_dir != repo:
        cmd += f" --core-dir {core_dir}"
    return cmd


def run_arch(ctx, name):
    cid = f"g2.arch.{name}"
    d = ctx.path("gate2", "arch", name, "x").parent
    elf, img = programs.build_arch(name, d)
    return cosim.cosim(cid, elf, img, d, _repro(ctx, "arch", name))


def run_rand(ctx, profile, index):
    cid = f"g2.rand.{profile}.{index:03d}"
    d = ctx.path("gate2", "rand", profile, f"{index:03d}", "x").parent
    elf, img = programs.build_rand(ctx.seed, profile, index, d)
    return cosim.cosim(cid, elf, img, d, _repro(ctx, "rand", profile, index))


def _engine_name(what):
    return "-".join(map(str, what if what[0] == "arch" else (what[0], what[1], f"{what[2]:03d}")))


def run_engine(ctx, what):
    name = _engine_name(what)
    cid = f"g2.engine.{name}"
    d = ctx.path("gate2", "engine", name, "x").parent
    if what[0] == "arch":
        elf, img = programs.build_arch(what[1], d)
    else:
        elf, img = programs.build_rand(ctx.seed, what[1], what[2], d)
    n = len(sail.run(elf, img.symbols["halt"], d).records)
    fast = cosim.verilator_core().run(img.words, n, cosim.max_cycles(n), d / "verilator")
    slow = PysimCore().run(img.words, n, cosim.max_cycles(n))
    problems = []
    if (fast.status, fast.cycles) != (slow.status, slow.cycles):
        problems.append(f"verilator {fast.status}/{fast.cycles} cycles, pysim {slow.status}/{slow.cycles}")
    if len(fast.retires) != len(slow.retires):
        problems.append(f"verilator {len(fast.retires)} records, pysim {len(slow.retires)}")
    for i, (a, b) in enumerate(zip(fast.retires, slow.retires)):
        if a != b:
            problems.append(f"record {i} differs:\n  verilator {a}\n  pysim     {b}")
            break
    return Check(cid, not problems,
                 "\n".join(problems) or f"{len(fast.retires)} records and {fast.cycles} cycles identical")


def run(ctx):
    full = ctx.mode == "full"
    checks = selftests.checks(ctx)
    arch = guarded("g2.arch", programs.arch_tests)
    jobs = []
    if isinstance(arch, Check):
        checks.append(arch)
    else:
        for name in arch:
            jobs.append((f"g2.arch.{name}", run_arch, ctx, name))
    for p in randgen.PROFILES:
        for i in range(RAND_FULL if full else RAND_FAST):
            jobs.append((f"g2.rand.{p}.{i:03d}", run_rand, ctx, p, i))
    for what in ENGINE_PROGRAMS:
        jobs.append((f"g2.engine.{_engine_name(what)}", run_engine, ctx, what))
    return checks + parallel(ctx, jobs)
