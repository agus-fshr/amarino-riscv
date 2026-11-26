"""Reproduce one gate 2 co-simulation check.

  python -m harness.gate2_cosim rand <profile> <index> [--seed S] [--core-dir DIR] [--out DIR]
  python -m harness.gate2_cosim arch <test-name>        [--core-dir DIR] [--out DIR]

Builds the program, runs Sail and the core, prints the comparison (the same
compare.log the gate writes) and exits 0 if the traces agree.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m harness.gate2_cosim", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=("rand", "arch"))
    ap.add_argument("what", nargs="+", help="<profile> <index> | <test-name>")
    ap.add_argument("--seed", type=lambda s: int(s, 0), default=0xA5A5_2026)
    ap.add_argument("--core-dir", type=Path, default=REPO)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    core_dir = args.core_dir.resolve()
    assert "amarino" not in sys.modules
    sys.path.insert(0, str(core_dir))
    os.environ["PYTHONPATH"] = os.pathsep.join([str(core_dir), str(REPO), os.environ.get("PYTHONPATH", "")])
    from harness.common.tools import configure_amaranth
    configure_amaranth()
    from harness.gate2_cosim import cosim, programs

    out = args.out or Path(tempfile.mkdtemp(prefix="g2-repro-"))
    if args.kind == "rand":
        profile, index = args.what[0], int(args.what[1])
        cid = f"g2.rand.{profile}.{index:03d}"
        elf, img = programs.build_rand(args.seed, profile, index, out)
    else:
        cid = f"g2.arch.{args.what[0]}"
        elf, img = programs.build_arch(args.what[0], out)
    check = cosim.cosim(cid, elf, img, out, " ".join(["python -m harness.gate2_cosim"] + sys.argv[1:]))
    print((out / "compare.log").read_text())
    print(f"program: {elf.with_suffix('.S') if args.kind == 'rand' else elf}\nlogs: {out}")
    print("PASS" if check.ok else "FAIL", check.id, check.data)
    return 0 if check.ok else 1


if __name__ == "__main__":
    sys.exit(main())
