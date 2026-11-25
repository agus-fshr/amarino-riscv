"""Gate 1: elaboration and lint.

Targets are Top in both instruction-memory modes plus every module class that
appears in Top's hierarchy, each elaborated on its own. Per target:

  g1.elab.<t>                       elaborates without raising
  g1.warn.<t>.<Category>@<file>     a Python warning raised while elaborating (one check per
                                    category and source file, so a new kind is a new check)
  g1.netlist.<t>                    converts to RTLIL/Verilog (catches combinational cycles)
  g1.yosys.<t>                      yosys `check` ran
  g1.yosys.<t>.<problem>            a problem yosys `check` reported (logic loop,
                                    conflicting drivers, undriven signal, ...)
  g1.verilator.<t>                  verilator --lint-only ran without errors
  g1.verilator.<t>.<CODE>           a verilator -Wall warning not waived in
                                    verilator_waivers.toml

and, on Top only:

  g1.port.<instance>.<port>         every input port of every submodule is driven by Top

Problem and warning checks exist only when the problem occurs. Gate 1 always runs in
full, so the runner flags an expected failure whose problem has gone away as STALE.
"""

import re
import subprocess
import traceback
import tomllib
from pathlib import Path

from amaranth.hdl._ast import SignalSet
from amaranth.lib import wiring

from harness.common import design
from harness.common.tools import ToolMissing, tool, cad_env
from harness.runner import Check, guarded

GATE = 1
NAME = "elaboration + lint"
COMPLETE_IN_FAST = True
HERE = Path(__file__).resolve().parent


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()


def _hierarchy_classes(frag, classes):
    for sub, _, _ in frag.subfragments:
        obj = sub.origins[0] if sub.origins else None
        if obj is not None:
            classes.setdefault(type(obj).__name__, type(obj))


def _warning_checks(tid, caught):
    out = {}
    for w in caught:
        src = design.core_relative(w.filename) or Path(w.filename).name
        cid = f"g1.warn.{tid}.{w.category.__name__}@{src}"
        out.setdefault(cid, []).append(f"{w.filename}:{w.lineno}: {w.message}")
    return [Check(cid, False, "\n".join(msgs)) for cid, msgs in sorted(out.items())]


_YOSYS_SCRIPT = "read_rtlil {il}; hierarchy -check -top top; proc; flatten; opt_clean; check"


def _yosys_check(ctx, tid, il_text):
    il = ctx.path("gate1", f"{tid}.il")
    il.write_text(il_text)
    log = ctx.path("gate1", f"{tid}.yosys.log")
    r = subprocess.run([tool("yosys"), "-l", str(log), "-q", "-p", _YOSYS_SCRIPT.format(il=il)],
                       capture_output=True, text=True, env=cad_env())
    checks = [Check(f"g1.yosys.{tid}", r.returncode == 0, f"log: {log}\n{r.stderr[-2000:]}")]
    problems = {}
    for line in log.read_text().splitlines():
        m = re.match(r"Warning: (.*)", line.strip())
        if m:
            kind = _slug(re.sub(r"`[^`']*'|\\\S+|\$\S+|\d+", "", m.group(1)))[:60]
            problems.setdefault(kind, []).append(m.group(1))
    for kind, msgs in sorted(problems.items()):
        checks.append(Check(f"g1.yosys.{tid}.{kind}", False, f"log: {log}\n" + "\n".join(msgs[:20])))
    return checks


def _waivers():
    with open(HERE / "verilator_waivers.toml", "rb") as f:
        table = tomllib.load(f)
    for code, reason in table.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"verilator waiver {code} needs a reason")
    return table


def _verilator(ctx, tid, v_text):
    v = ctx.path("gate1", f"{tid}.v")
    v.write_text(v_text)
    waived = _waivers()
    cmd = [tool("verilator"), "--lint-only", "-Wall", "--top-module", "top", str(v)]
    cmd += [f"-Wno-{code}" for code in waived]
    r = subprocess.run(cmd, capture_output=True, text=True, env=cad_env())
    log = ctx.path("gate1", f"{tid}.verilator.log")
    log.write_text(r.stdout + r.stderr)
    codes = {}
    errors = []
    for line in (r.stdout + r.stderr).splitlines():
        m = re.match(r"%Warning-([A-Z0-9_]+):", line)
        if m:
            codes.setdefault(m.group(1), []).append(line)
        elif line.startswith("%Error") and not line.startswith("%Error: Exiting due to"):
            errors.append(line)
    checks = [Check(f"g1.verilator.{tid}", not errors and (r.returncode == 0 or bool(codes)),
                    f"log: {log}\n" + "\n".join(errors[:20]))]
    for code, lines in sorted(codes.items()):
        checks.append(Check(f"g1.verilator.{tid}.{code}", False, f"log: {log}\n" + "\n".join(lines[:20])))
    return checks


def _port_checks(imem, frag):
    """Every input port of every component instantiated by Top must be driven by Top."""
    checks = []
    driven = SignalSet()
    for stmts in frag.statements.values():
        for s in stmts:
            driven |= s._lhs_signals()
    for sub, inst, _ in frag.subfragments:
        obj = sub.origins[0] if sub.origins else None
        if not isinstance(obj, wiring.Component):
            continue
        for path, member, value in obj.signature.flatten(obj):
            if member.flow != wiring.In:
                continue
            port = f"{inst}.{'.'.join(map(str, path))}"
            ok = all(s in driven for s in value._rhs_signals())
            checks.append(Check(f"g1.port.{port}", ok, "" if ok else
                                f"input {port} is not driven by Top (imem={imem}); it reads as its reset value"))
    return checks


def _merge(checks):
    """The same check id from both imem modes: a failure wins."""
    merged = {}
    for c in checks:
        if c.id not in merged or (merged[c.id].ok and not c.ok):
            merged[c.id] = c
    return list(merged.values())


def _design_error(e):
    return f"{type(e).__name__}: {e}\n{traceback.format_exc()}"


def _target_checks(ctx, tid, factory):
    # Any exception raised while elaborating or converting the core is a design
    # failure (driver conflict, combinational cycle, bad Python), not a harness error.
    try:
        _, caught, _ = design.elaborate(factory())
    except Exception as e:  # noqa: BLE001
        return [Check(f"g1.elab.{tid}", False, _design_error(e) +
                      "\n(module classes must elaborate standalone with no constructor arguments)")]
    checks = [Check(f"g1.elab.{tid}", True)] + _warning_checks(tid, caught)
    try:
        il_text, v_text = design.to_rtlil(factory()), design.to_verilog(factory())
    except ToolMissing:
        raise
    except Exception as e:  # noqa: BLE001
        return checks + [Check(f"g1.netlist.{tid}", False, _design_error(e))]
    checks.append(Check(f"g1.netlist.{tid}", True))
    checks += guarded_list(f"g1.yosys.{tid}", lambda: _yosys_check(ctx, tid, il_text))
    checks += guarded_list(f"g1.verilator.{tid}", lambda: _verilator(ctx, tid, v_text))
    return checks


def guarded_list(cid, fn):
    r = guarded(cid, fn)
    return [r] if isinstance(r, Check) else r


def run(ctx):
    checks, classes = [], {}
    for imem in ("internal", "external"):
        tid = f"top-{imem}"
        try:
            frag, _, _ = design.elaborate(design.new_top(imem))
        except Exception:  # noqa: BLE001 - reported by g1.elab.<tid> below
            continue
        _hierarchy_classes(frag, classes)
        checks += _port_checks(imem, frag)
    checks = _merge(checks)
    targets = [(f"top-{imem}", lambda imem=imem: design.new_top(imem)) for imem in ("internal", "external")]
    targets += sorted(classes.items())
    for tid, factory in targets:
        checks += guarded_list(f"g1.elab.{tid}", lambda: _target_checks(ctx, tid, factory))
    return checks
