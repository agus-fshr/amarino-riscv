"""python -m harness [--fast | --full] [--gate N ...] [--core-dir DIR] [--json FILE]

Runs the correctness gates against the core in amarino/ (or DIR/amarino).
Exit status 0 means green: every check passed or is a listed expected failure.
"""

import argparse
import importlib
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Gate number -> module. Each module defines GATE, NAME and run(ctx) -> [Check].
GATES = {
    1: "harness.gate1_lint",
}

DEFAULT_SEED = 0xA5A5_2026


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--fast", dest="mode", action="store_const", const="fast",
                      help="quick subset for the edit/test loop (default)")
    mode.add_argument("--full", dest="mode", action="store_const", const="full",
                      help="every check; this is the judge of correctness")
    ap.add_argument("--gate", type=int, action="append", choices=sorted(GATES),
                    help="run only these gates (repeatable)")
    ap.add_argument("--core-dir", type=Path, default=REPO,
                    help="directory containing the amarino package to test (default: repo root)")
    ap.add_argument("--out", type=Path, default=None, help="log directory (default: harness-out/<time>)")
    ap.add_argument("--json", default=None, help="also write results as JSON")
    ap.add_argument("--seed", type=lambda s: int(s, 0), default=DEFAULT_SEED,
                    help=f"random-program seed (default {DEFAULT_SEED:#x})")
    ap.add_argument("-j", "--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)))
    ap.add_argument("-v", "--verbose", action="store_true", help="also list passing checks")
    args = ap.parse_args(argv)
    args.mode = args.mode or "fast"

    core_dir = args.core_dir.resolve()
    if not (core_dir / "amarino" / "__init__.py").exists():
        ap.error(f"{core_dir} has no amarino package")
    # The core under test must be the first amarino on the path, and nothing may have
    # imported another copy yet.
    assert "amarino" not in sys.modules
    sys.path.insert(0, str(core_dir))
    os.environ["PYTHONPATH"] = os.pathsep.join([str(core_dir), str(REPO), os.environ.get("PYTHONPATH", "")])

    from harness.common.tools import configure_amaranth
    from harness.runner import Context, run
    configure_amaranth()

    out = args.out or REPO / "harness-out" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    ctx = Context(mode=args.mode, out=out, jobs=args.jobs, seed=args.seed)
    print(f"core: {core_dir / 'amarino'}\nlogs: {out}\nseed: {args.seed:#x}\n")

    gates = [importlib.import_module(GATES[n]) for n in (args.gate or sorted(GATES))]
    ok = run(gates, ctx, as_json=args.json, verbose=args.verbose)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
