"""Gate 3: formal verification with riscv-formal + SymbiYosys.

Top(imem="external") is exported to Verilog and wrapped by wrapper.sv, which feeds an
unconstrained instruction word every cycle. riscv-formal's genchecks.py turns
checks.cfg into one sby task per check (RV32I instruction checks plus the
consistency checks); each task is a bounded model check of every possible
instruction stream from reset. Depths are justified in DEPTH.md.

Check ids:

  g3.<check>               the riscv-formal check (e.g. g3.insn_add_ch0, g3.reg_ch0,
                           g3.liveness_ch0, g3.cover): PASS, FAIL, or ERROR if sby
                           did not produce a verdict
  g3.<check>.<assertion>   one per assertion the solver could violate (sby runs
                           smtbmc --keep-going, so every failing assertion is
                           found, not just the first). Exists only when that
                           assertion fails, so a listed failure of one assertion
                           (e.g. insn_add's pc_wdata) cannot hide a new failure of
                           another (e.g. its rd_wdata).

Vacuity: sby's smtbmc runs with --presat, so if the assumptions of a check cannot
be met at the check step (e.g. no instruction can retire by then) the task ends
in an error, which is reported as ERROR, never as a pass.

Fast mode runs FAST at the same depths as full mode. Every counterexample is kept
under <out>/gate3/rvf/cores/amarino/checks/<check>/ together with a readable
retirement trace (<check>/engine_0/trace*.txt).
"""

import re
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.common import design
from harness.common.tools import ToolMissing, tool, cad_env
from harness.runner import Check, guarded
from harness.gate3_formal.trace import render

GATE = 3
NAME = "formal (riscv-formal)"
COMPLETE_IN_FAST = False
HERE = Path(__file__).resolve().parent
CORE = "amarino"

# Fixed fast subset, run at the same depths as full mode.
FAST = ["insn_add_ch0", "insn_addi_ch0", "insn_lui_ch0", "insn_sub_ch0", "insn_beq_ch0",
        "insn_jal_ch0", "pc_fwd_ch0", "unique_ch0", "cover"]

MAX_JOBS = 2                 # sby/solver processes at once (memory)
TIMEOUT = 4 * 3600           # per check; a timeout is an ERROR, never a pass


def _setup(ctx, top_v):
    """Lay out a riscv-formal tree under the run directory and run genchecks.py."""
    rvf = tool("riscv-formal")
    root = ctx.path("gate3", "rvf", "x").parent
    coredir = root / "cores" / CORE
    if root.exists():
        shutil.rmtree(root)
    coredir.mkdir(parents=True)
    for d in ("checks", "insns"):
        (root / d).symlink_to(Path(rvf) / d)
    shutil.copy(HERE / "wrapper.sv", coredir / "wrapper.sv")
    shutil.copy(HERE / "checks.cfg", coredir / "checks.cfg")
    (coredir / "top.v").write_text(top_v)
    r = subprocess.run([sys.executable, str(Path(rvf) / "checks" / "genchecks.py")], cwd=coredir,
                       capture_output=True, text=True)
    (coredir / "genchecks.log").write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"genchecks.py failed:\n{r.stdout}\n{r.stderr}")
    checks = coredir / "checks"
    for sby in checks.glob("*.sby"):
        text = sby.read_text()
        if "\nmode bmc\n" in text:
            # Report every violated assertion, not only the first.
            text = re.sub(r"^smtbmc ", "smtbmc --keep-going ", text, flags=re.M)
            sby.write_text(text)
    return checks


_ASSERT_RE = re.compile(r"engine_\d+: .*Assert failed in \S+ (\S+?):(\d+)\.")
_STEP_RE = re.compile(r"engine_\d+: .*Checking assertions in step (\d+)")
_TRACE_RE = re.compile(r"engine_\d+: .*Writing trace to VCD file: (\S+)")
_COVER_RE = re.compile(r"Reached cover statement in step (\d+) at \S+ (\S+?):(\d+)\.")
_UNREACHED_RE = re.compile(r"Unreached cover statement at \S+ (\S+?):(\d+)\.")


def _failed_assertions(out):
    """[(src, line, step, vcd-relative-to-task-or-None)] from smtbmc's own log lines.
    (sby's summary truncates the list after five assertions per trace.) smtbmc prints
    the assertions a trace violates, then writes that trace."""
    found, pending, step = [], [], None
    for line in out.splitlines():
        m = _STEP_RE.search(line)
        if m:
            step = int(m.group(1))
            continue
        m = _ASSERT_RE.search(line)
        if m:
            pending.append((m.group(1), int(m.group(2)), step))
            continue
        m = _TRACE_RE.search(line)
        if m:
            found += [(src, ln, st, m.group(1)) for src, ln, st in pending]
            pending = []
    return found + [(src, ln, st, None) for src, ln, st in pending]


def _assert_label(src_file, line, rvf_checks):
    """A stable name for an assertion from its source text, e.g. 'spec_rd_wdata-eq-rd_wdata'."""
    path = Path(rvf_checks) / src_file
    try:
        lines = path.read_text().splitlines()
        text = lines[line - 1]
    except (OSError, IndexError):
        return f"{Path(src_file).stem}-L{line}"
    m = re.search(r"assert\s*\((.*)\)\s*;", text)
    expr = (m.group(1) if m else text).replace("`", "")
    expr = re.sub(r"\[[^\]]*\]", "", expr)   # drop channel/byte subscripts
    # spec_X == X  and  rvformal_addr_eq(spec_X, X)  ->  X
    m = (re.fullmatch(r"\s*spec_(\w+)\s*==\s*(\w+)\s*", expr)
         or re.fullmatch(r"\s*rvformal_addr_eq\(\s*spec_(\w+)\s*,\s*(\w+)\s*\)\s*", expr))
    if m and m.group(1) == m.group(2):
        expr = m.group(1)
    label = re.sub(r"[^A-Za-z0-9_]+", "-", expr.replace("==", " eq ")
                   .replace("!=", " ne ").replace("!", " not ")).strip("-")
    if sum(1 for ln in lines if ln.strip() == text.strip()) > 1:
        label += f"-L{line}"
    return label


def _run_one(checks_dir, name, rvf_checks):
    task = checks_dir / name
    t0 = time.time()
    try:
        r = subprocess.run([tool("sby"), "-f", f"{name}.sby"], cwd=checks_dir, capture_output=True,
                           text=True, env=cad_env(), timeout=TIMEOUT)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        secs = time.time() - t0
        return [Check(f"g3.{name}", None, f"sby timed out after {secs:.0f}s (limit {TIMEOUT}s); "
                      f"log: {task}/logfile.txt", data={"seconds": round(secs, 1)})]
    secs = time.time() - t0
    log = task / "logfile.txt"
    status = (task / "status").read_text().split()[0] if (task / "status").exists() else "UNKNOWN"
    data = {"seconds": round(secs, 1), "status": status}

    if status == "PASS":
        detail = f"PASS in {secs:.0f}s; log: {log}"
        if name == "cover":
            reached = [(int(st), f"{f}:{ln}") for st, f, ln in _COVER_RE.findall(out)]
            data["cover_steps"] = sorted(reached)
            detail += "\ncover statements reached at steps: " + ", ".join(
                f"{loc}@{s}" for s, loc in sorted(reached))
        return [Check(f"g3.{name}", True, detail, data=data)]

    if status != "FAIL":
        tail = "\n".join(out.strip().splitlines()[-15:])
        return [Check(f"g3.{name}", None, f"sby status {status} (no verdict) after {secs:.0f}s; "
                      f"log: {log}\n{tail}", data=data)]

    failed = {}
    for src, line, step, trace in _failed_assertions(out):
        vcd = None
        if trace:
            vcd = task / trace
            txt = vcd.with_suffix(".txt")
            if vcd.exists() and not txt.exists():
                try:
                    txt.write_text(render(vcd))
                except Exception as e:  # noqa: BLE001 - a rendering problem must not hide the verdict
                    txt.write_text(f"could not render {vcd}: {e!r}\n")
        failed.setdefault(_assert_label(src, line, rvf_checks), (f"{src}:{line}", step, vcd))
    if name == "cover":
        unreached = [f"{f}:{ln}" for f, ln in _UNREACHED_RE.findall(out)]
        return [Check(f"g3.{name}", False, f"cover statements not reached within depth: "
                      f"{', '.join(unreached)}; log: {log}", data=data)]
    data["assertions"] = sorted(failed)
    lines = [f"FAIL in {secs:.0f}s; log: {log}"]
    for label, (loc, step, trace) in sorted(failed.items()):
        lines.append(f"  {label} ({loc}, step {step})"
                     + (f": trace {trace.with_suffix('.txt')} (vcd {trace})" if trace else ""))
    if not failed:
        lines.append("  (no failed assertion found in the sby output)")
    checks = [Check(f"g3.{name}", False, "\n".join(lines), data=data)]
    for label, (loc, step, trace) in sorted(failed.items()):
        checks.append(Check(f"g3.{name}.{label}", False,
                            f"{loc} violated at step {step}"
                            + (f"; trace {trace.with_suffix('.txt')} (vcd {trace})" if trace else "")
                            + f"; log: {log}", data={"seconds": round(secs, 1)}))
    return checks


def run(ctx):
    tool("riscv-formal"), tool("sby"), tool("yosys")   # ToolMissing -> ERROR
    try:
        top_v = design.to_verilog(design.new_top("external"))
    except ToolMissing:
        raise
    except Exception as e:  # noqa: BLE001 - a core that doesn't export is a design failure (see gate 1)
        return [Check("g3.netlist", False, f"could not export Top(imem='external') to Verilog: {e!r}\n"
                      f"{traceback.format_exc()}")]
    checks_dir = _setup(ctx, top_v)
    available = sorted(p.stem for p in checks_dir.glob("*.sby"))
    if ctx.mode == "fast":
        missing = [n for n in FAST if n not in available]
        if missing:
            raise RuntimeError(f"fast-mode checks not generated by genchecks.py: {missing}")
        names = FAST
    else:
        names = available
    rvf_checks = Path(tool("riscv-formal")) / "checks"

    def one(name):
        r = guarded(f"g3.{name}", _run_one, checks_dir, name, rvf_checks)
        return [r] if isinstance(r, Check) else r

    # Longest first, so the slow checks don't end up alone at the tail.
    order = sorted(names, key=lambda n: (not n.startswith(("reg", "liveness")), n))
    with ThreadPoolExecutor(max_workers=max(1, min(ctx.jobs, MAX_JOBS))) as pool:
        results = dict(zip(order, pool.map(one, order)))
    out = [c for n in names for c in results[n]]
    summary = ctx.path("gate3", "summary.txt")
    summary.write_text("".join(
        f"{c.id:60} {'PASS' if c.ok else 'ERROR' if c.ok is None else 'FAIL':5} "
        f"{c.data.get('seconds', ''):>8}\n" for c in out if c.id.count(".") == 1))
    return out

