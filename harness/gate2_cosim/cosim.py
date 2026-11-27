"""Run one program on Sail and on the core and compare the traces."""

import threading
from pathlib import Path

from harness.common.coresim import VerilatorCore
from harness.gate2_cosim import compare, rv32i, sail
from harness.runner import Check

_core_lock = threading.Lock()
_core = {}


def verilator_core():
    """One VerilatorCore per run (built once, cached by source hash). A build failure is
    re-raised for every check that needs it, so each of them reports ERROR."""
    with _core_lock:
        if "core" not in _core:
            try:
                _core["core"] = VerilatorCore()
            except Exception as e:  # noqa: BLE001 - re-raised below for every caller
                _core["core"] = e
        core = _core["core"]
    if isinstance(core, Exception):
        raise core
    return core


def max_cycles(n):
    """Cycle budget for retiring n records: far above any plausible CPI for this pipeline."""
    return 20 * n + 1000


def run_core(engine, image, n, workdir):
    run = engine.run(image.words, n, max_cycles(n), workdir)
    return run, [compare.from_core(r) for r in run.retires]


def cosim(check_id, elf, image, workdir, repro, engine=None):
    """Check that the core's first L records equal Sail's L records (L = reference length)."""
    workdir = Path(workdir)
    ref = sail.run(elf, image.symbols["halt"], workdir)
    n = len(ref.records)
    engine = engine or verilator_core()
    run, dut = run_core(engine, image, n, workdir)
    res = compare.compare(ref.records, dut)
    log = workdir / "compare.log"
    head = (f"{check_id}\nelf: {elf}\nreference: {n} records, ended at {ref.ended}\n"
            f"core: {len(dut)} records in {run.cycles} cycles ({run.status})\n"
            f"reproduce: {repro}\n\n")
    log.write_text(head + compare.report(res, ref.records, dut))
    if res.ok:
        return Check(check_id, True, f"{n} records identical", data=res.data())
    i = res.matched
    if res.field == "missing":
        what = f"core retired only {len(dut)} of {n} ({run.status} after {run.cycles} cycles)"
    else:
        r = ref.records[i]
        what = (f"record {i}/{n} field {res.field} at pc {r.pc_rdata:#010x} {rv32i.disasm(r.insn)}: "
                f"ref {getattr(r, res.field):#x} core {getattr(dut[i], res.field):#x}")
    return Check(check_id, False, f"diverges: {what}\nlog: {log}\nreproduce: {repro}",
                 data=res.data())
