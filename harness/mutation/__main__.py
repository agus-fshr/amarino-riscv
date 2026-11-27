"""python -m harness.mutation [--fast | --full] [--gate N ...] [--mutant ID ...]

Validates the harness itself: runs the gates on the real core (the baseline) and on
each mutant in mutants.toml, and reports which checks catch which mutant.

A check kills a mutant when:
  - it passes on the baseline and fails (or errors) on the mutant, or
  - it is a co-simulation check (it reports data["matched"]) and the mutant's trace
    matches the reference for fewer retirements than the baseline's does, i.e. the
    mutant goes wrong before the baseline's own first divergence, or
  - it does not exist on the baseline and fails on the mutant (e.g. a new lint finding).

The run is green when every mutant is killed, except mutants documented as
equivalent on the current core, which must survive.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEFAULT_GATES = (1, 2, 3)          # gate 4 is report-only


def load_mutants():
    with open(HERE / "mutants.toml", "rb") as f:
        mutants = tomllib.load(f)["mutant"]
    ids = [m["id"] for m in mutants]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate mutant ids")
    return mutants


def apply(mutant, dest):
    """Copy amarino/ to dest/amarino and apply the mutant. Returns an error string or None."""
    shutil.copytree(REPO / "amarino", dest / "amarino",
                    ignore=shutil.ignore_patterns("__pycache__"))
    path = dest / mutant["file"]
    text = path.read_text()
    n = text.count(mutant["old"])
    if n != 1:
        return f"{mutant['file']}: text to replace occurs {n} times (must be exactly once)"
    path.write_text(text.replace(mutant["old"], mutant["new"]))
    return None


def run_harness(core_dir, out, mode, gates, jobs, seed):
    cmd = [sys.executable, "-m", "harness", f"--{mode}", "--core-dir", str(core_dir),
           "--out", str(out / "logs"), "--json", str(out / "result.json"), "-j", str(jobs)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    for g in gates:
        cmd += ["--gate", str(g)]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    (out / "harness.log").write_text(r.stdout + r.stderr)
    if not (out / "result.json").exists():
        raise RuntimeError(f"harness produced no results (exit {r.returncode}); see {out / 'harness.log'}")
    data = json.loads((out / "result.json").read_text())
    return {c["id"]: c for c in data["checks"]}, time.time() - t0


def kills(base, mut):
    """[(check id, gate, why)] for every check that distinguishes the mutant."""
    out = []
    for cid, m in mut.items():
        b = base.get(cid)
        if m.get("info"):
            continue
        if b is None:
            if m["ok"] is not True:
                out.append((cid, m["gate"], "new failing check"))
            continue
        if b["ok"] is True and m["ok"] is not True:
            out.append((cid, m["gate"], "passes on baseline, " + ("errors" if m["ok"] is None else "fails") + " on mutant"))
            continue
        bm, mm = b.get("data", {}).get("matched"), m.get("data", {}).get("matched")
        if bm is not None and mm is not None and mm < bm:
            out.append((cid, m["gate"], f"diverges earlier: {mm} matching retirements vs {bm} on baseline"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.mutation", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--fast", dest="mode", action="store_const", const="fast")
    mode.add_argument("--full", dest="mode", action="store_const", const="full")
    ap.add_argument("--gate", type=int, action="append", help=f"gates to run (default {DEFAULT_GATES})")
    ap.add_argument("--mutant", action="append", help="only these mutant ids")
    ap.add_argument("--seed", default=None)
    ap.add_argument("-j", "--jobs", type=int, default=2)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    mode = args.mode or "fast"
    gates = args.gate or list(DEFAULT_GATES)
    mutants = load_mutants()
    if args.mutant:
        unknown = set(args.mutant) - {m["id"] for m in mutants}
        if unknown:
            ap.error(f"unknown mutants: {sorted(unknown)}")
        mutants = [m for m in mutants if m["id"] in args.mutant]

    out = args.out or REPO / "harness-out" / f"mutation-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"mutation run: {mode}, gates {gates}, {len(mutants)} mutants\nlogs: {out}\n", flush=True)

    base, secs = run_harness(REPO, out / "baseline", mode, gates, args.jobs, args.seed)
    print(f"baseline: {len(base)} checks in {secs:.0f}s", flush=True)

    rows, red = [], []
    for m in mutants:
        d = out / m["id"]
        d.mkdir()
        err = apply(m, d)
        if err is None:
            try:
                res, secs = run_harness(d, d, mode, gates, args.jobs, args.seed)
                k = kills(base, res)
            except RuntimeError as e:
                err = str(e)
        if err is not None:
            status, k = "ERROR", []
            detail = err
        else:
            by_gate = {}
            for cid, g, why in k:
                by_gate.setdefault(g, []).append((cid, why))
            if k:
                status = "KILLED" if "equivalent" not in m else "KILLED (listed as equivalent: update mutants.toml)"
            else:
                status = "equivalent" if "equivalent" in m else "SURVIVED"
            detail = "; ".join(f"g{g}: {len(v)} checks, e.g. {v[0][0]} ({v[0][1]})" for g, v in sorted(by_gate.items()))
            (d / "kills.json").write_text(json.dumps(k, indent=1))
        ok = status == "KILLED" or status == "equivalent"
        if not ok:
            red.append(m["id"])
        gates_hit = sorted({g for _, g, _ in k})
        rows.append((m["id"], m["what"], status, gates_hit, detail or m.get("equivalent", "")))
        print(f"{status:10} {m['id']:28} gates {gates_hit}  {detail or m.get('equivalent', '')}", flush=True)

    report = ["| mutant | what it breaks | result | caught by gates | example |", "|---|---|---|---|---|"]
    cell = lambda t: str(t).replace("|", "\\|")
    for mid, what, status, gates_hit, detail in rows:
        report.append(f"| `{mid}` | {cell(what)} | {status} | {', '.join(map(str, gates_hit)) or '-'} | {cell(detail)} |")
    (out / "report.md").write_text("\n".join(report) + "\n")
    print(f"\n{'RED' if red else 'GREEN'}: {len(rows) - len(red)}/{len(rows)} mutants as expected; report: {out / 'report.md'}")
    for mid in red:
        print(f"  not as expected: {mid}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
