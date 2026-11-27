"""Gate runner: runs checks, applies the expected-failures list, reports.

Verdicts for each check:
  PASS   passed, not expected to fail
  XFAIL  failed, and listed in spec/expected_failures.toml        (green)
  FAIL   failed, not listed                                      (red)
  XPASS  passed, but listed as expected to fail; remove the entry (red)
  ERROR  could not be run (tool missing, crash); never satisfies an
         expected failure                                        (red)
  STALE  listed, but no check with that id ran in a gate that ran
         completely (--full, or a gate that is always complete)      (red)
  INFO   report-only result (gate 4); never red

A run is green only if there is no FAIL, XPASS, ERROR or STALE.
"""

import json
import time
import tomllib
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path

SPEC = Path(__file__).resolve().parent / "spec"
RED = {"FAIL", "XPASS", "ERROR", "STALE"}


@dataclass
class Check:
    id: str
    ok: bool | None              # None: could not be run
    detail: str = ""
    info: bool = False           # report-only
    data: dict = field(default_factory=dict)


@dataclass
class Context:
    mode: str                    # "fast" or "full"
    out: Path                    # per-run output directory
    jobs: int
    seed: int

    def path(self, *parts):
        p = self.out.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def guarded(check_id, fn, *args, **kwargs):
    """Run fn -> Check; turn an exception into an ERROR check carrying the traceback."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - every failure must surface as a result
        from harness.common.tools import ToolMissing
        if isinstance(e, ToolMissing):
            return Check(check_id, None, str(e))
        return Check(check_id, None, f"harness error: {e!r}\n{traceback.format_exc()}")


def parallel(ctx, jobs):
    """Run [(check_id, fn, args...)] with ctx.jobs workers."""
    with ThreadPoolExecutor(max_workers=max(1, ctx.jobs)) as pool:
        futs = [pool.submit(guarded, cid, fn, *args) for cid, fn, *args in jobs]
        return [f.result() for f in futs]


def load_expected_failures(path=SPEC / "expected_failures.toml"):
    with open(path, "rb") as f:
        table = tomllib.load(f)
    for key, reason in table.items():
        if not isinstance(reason, str) or not reason.strip() or "\n" in reason.strip():
            raise ValueError(f"{path}: {key!r} needs a one-line reason")
    return table


def verdict(check, expected):
    if check.info:
        return "INFO"
    if check.ok is None:
        return "ERROR"
    if check.ok:
        return "XPASS" if check.id in expected else "PASS"
    return "XFAIL" if check.id in expected else "FAIL"


def run(gates, ctx, as_json=None, verbose=False):
    expected = load_expected_failures()
    results = []
    for gate in gates:
        t0 = time.time()
        print(f"== gate {gate.GATE}: {gate.NAME} ({ctx.mode})", flush=True)
        checks = guarded(f"g{gate.GATE}.run", lambda: list(gate.run(ctx)))
        if isinstance(checks, Check):
            checks = [checks]
        for c in checks:
            v = verdict(c, expected)
            results.append((gate.GATE, c, v))
            if v != "PASS" or verbose:
                line = c.detail.strip().splitlines()[0] if c.detail.strip() else ""
                print(f"  {v:5} {c.id}" + (f"  — {line}" if line and v != "XFAIL" else
                                             f"  — {expected[c.id]}" if v == "XFAIL" else ""))
        counts = {}
        for _, _, v in [r for r in results if r[0] == gate.GATE]:
            counts[v] = counts.get(v, 0) + 1
        print(f"  {sum(counts.values())} checks in {time.time() - t0:.1f}s: "
              + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())), flush=True)

    stale = []
    complete = [g for g in gates if ctx.mode == "full" or getattr(g, "COMPLETE_IN_FAST", False)]
    if complete:
        ran_gates = {f"g{g.GATE}." for g in complete}
        seen = {c.id for _, c, _ in results}
        stale = [k for k in expected if any(k.startswith(p) for p in ran_gates) and k not in seen]
        for k in stale:
            print(f"  STALE {k}  — listed in expected_failures.toml but no such check ran")

    red = [(c.id, v) for _, c, v in results if v in RED] + [(k, "STALE") for k in stale]
    print(f"\n{'RED' if red else 'GREEN'}: {len(results)} checks, {len(red)} red")
    for cid, v in red:
        print(f"  {v:5} {cid}")

    if as_json:
        Path(as_json).write_text(json.dumps({
            "mode": ctx.mode, "seed": ctx.seed, "green": not red,
            "checks": [dict(asdict(c), gate=g, verdict=v) for g, c, v in results],
            "stale": stale,
        }, indent=1))
    return not red
