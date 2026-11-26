"""Measure, on the current core, the numbers DEPTH.md uses to justify gate 3's depths.

    python -m harness.gate3_formal.measure_depth [--core-dir DIR] [--out DIR]

Every number is proved by sby over all instruction streams (same wrapper, same
solver), by re-targeting the generated riscv-formal tasks:

  first   earliest step the first RVFI record can appear (cover, no skip) and the
          latest (hang check: "some record before step c" for growing c)
  count   how many records can have appeared by the check step D=20 (cover at D)
  gap     the longest possible distance between consecutive records (liveness
          check with trigger t and a window of g cycles, for t = 5..12)

Rerun it whenever the pipeline changes and update DEPTH.md.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _sby(checks_dir, name, text):
    from harness.common.tools import tool, cad_env
    (checks_dir / f"{name}.sby").write_text(text)
    r = subprocess.run([tool("sby"), "-f", f"{name}.sby"], cwd=checks_dir, capture_output=True,
                       text=True, env=cad_env())
    status = (checks_dir / name / "status")
    return (status.read_text().split()[0] if status.exists() else "ERROR"), r.stdout


def _retarget(text, check, depth=None, trig=None, skip=True):
    """Move a generated task's check (and trigger) cycle."""
    text = re.sub(r"^depth \d+", f"depth {check + 1}", text, flags=re.M)
    text = re.sub(r"^skip \d+\n", f"skip {check}\n" if skip else "", text, flags=re.M)
    text = re.sub(r"RISCV_FORMAL_CHECK_CYCLE \d+", f"RISCV_FORMAL_CHECK_CYCLE {check}", text)
    if trig is not None:
        text = re.sub(r"RISCV_FORMAL_TRIG_CYCLE \d+", f"RISCV_FORMAL_TRIG_CYCLE {trig}", text)
    return text


def _with_covers(text, stmts):
    i = text.index("[file cover_stmts.vh]")
    return text[:i] + "[file cover_stmts.vh]\n" + "\n".join(stmts) + "\n"


def measure(checks_dir, depth=20):
    cover = (checks_dir / "cover.sby").read_text()
    hang = (checks_dir / "hang.sby").read_text()
    live = (checks_dir / "liveness_ch0.sby").read_text()
    res = {}

    # Earliest step of the n-th record.
    n_max = depth
    _, out = _sby(checks_dir, "m_first", _with_covers(_retarget(cover, depth, skip=False), [
        f"always @* if (!reset) cover (channel[0].cnt_insns == {n});" for n in range(1, n_max + 1)]))
    earliest = {}
    for step, line in re.findall(r"Reached cover statement in step (\d+) at \S+ cover_stmts.vh:(\d+)\.", out):
        earliest[int(line)] = int(step)
    res["earliest_step_of_nth_record"] = dict(sorted(earliest.items()))

    # Records that can have appeared by the check step.
    _, out = _sby(checks_dir, "m_count", _with_covers(cover, [
        f"always @* if (!reset && check) cover (channel[0].cnt_insns == {n});" for n in range(0, depth + 2)]))
    counts = sorted(int(line) - 1 for _, line in
                    re.findall(r"Reached cover statement in step (\d+) at \S+ cover_stmts.vh:(\d+)\.", out))
    res[f"records_possible_by_step_{depth}"] = (counts[0], counts[-1]) if counts else None

    # Latest first record: hang at check c passes iff a record always appears before step c.
    latest = None
    for c in range(1, depth + 1):
        st, _ = _sby(checks_dir, f"m_hang{c}", _retarget(hang, c))
        if st == "PASS":
            latest = c - 1
            break
        if st != "FAIL":
            raise RuntimeError(f"hang@{c}: {st}")
    res["latest_step_of_first_record"] = latest

    # Longest gap between consecutive records.
    gaps = {}
    for t in range(5, 13):
        for g in range(1, depth):
            st, _ = _sby(checks_dir, f"m_live{t}_{g}", _retarget(live, t + g, trig=t))
            if st == "PASS":
                gaps[t] = g
                break
            if st != "FAIL":
                raise RuntimeError(f"liveness trig {t} window {g}: {st}")
    res["max_gap_after_record_at_step"] = gaps
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core-dir", type=Path, default=REPO)
    ap.add_argument("--out", type=Path, default=REPO / "harness-out" / f"depth-{time.strftime('%Y%m%d-%H%M%S')}")
    args = ap.parse_args()
    sys.path.insert(0, str(args.core_dir.resolve()))
    os.environ["PYTHONPATH"] = os.pathsep.join([str(args.core_dir.resolve()), str(REPO), os.environ.get("PYTHONPATH", "")])
    from harness.common import design
    from harness.common.tools import configure_amaranth
    from harness.gate3_formal import _setup
    from harness.runner import Context
    configure_amaranth()
    ctx = Context(mode="full", out=args.out, jobs=1, seed=0)
    checks_dir = _setup(ctx, design.to_verilog(design.new_top("external")))
    for k, v in measure(checks_dir).items():
        print(f"{k}: {v}")
    print(f"tasks: {checks_dir}/m_*")


if __name__ == "__main__":
    main()
