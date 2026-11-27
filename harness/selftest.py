"""Self-test of the runner's verdict logic: `python -m harness.selftest`.

Checks that the expected-failures mechanism cannot turn a run green when it shouldn't.
"""

import tempfile
import types
from pathlib import Path

from harness import runner
from harness.runner import Check, Context, run


def fake_gate(checks, complete=True):
    return types.SimpleNamespace(GATE=9, NAME="selftest", COMPLETE_IN_FAST=complete, run=lambda ctx: checks)


def green(checks, expected, mode="fast", complete=True):
    with tempfile.TemporaryDirectory() as d:
        spec = Path(d) / "xf.toml"
        spec.write_text("".join(f'"{k}" = "{v}"\n' for k, v in expected.items()))
        orig = runner.load_expected_failures
        runner.load_expected_failures = lambda: orig(spec)
        try:
            ctx = Context(mode=mode, out=Path(d), jobs=1, seed=0)
            return run([fake_gate(checks, complete)], ctx)
        finally:
            runner.load_expected_failures = orig


def main():
    ok, bad = Check("g9.a", True), Check("g9.b", False, "broken")
    err, info = Check("g9.c", None, "tool missing"), Check("g9.d", False, info=True)
    cases = [
        ("all pass", [ok], {}, True),
        ("unexpected failure", [ok, bad], {}, False),
        ("expected failure", [ok, bad], {"g9.b": "known"}, True),
        ("unexpected pass (XPASS)", [ok, bad], {"g9.a": "known", "g9.b": "known"}, False),
        ("error never satisfies an xfail", [err], {"g9.c": "known"}, False),
        ("error is red", [err], {}, False),
        ("info never red", [info], {}, True),
        ("stale entry in complete gate", [ok], {"g9.zz": "gone"}, False),
    ]
    failures = 0
    for name, checks, expected, want in cases:
        got = green(checks, expected)
        status = "ok " if got == want else "BAD"
        failures += got != want
        print(f"{status} {name}: green={got}, want {want}")
    # Stale entries are not judged when the gate ran only partially (fast mode).
    got = green([ok], {"g9.zz": "gone"}, mode="fast", complete=False)
    failures += got is not True
    print(f"{'ok ' if got else 'BAD'} partial gate ignores entries it did not run")
    # Reasons are mandatory and single-line.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "xf.toml"
        p.write_text('"g9.b" = ""\n')
        try:
            runner.load_expected_failures(p)
            print("BAD empty reason accepted"); failures += 1
        except ValueError:
            print("ok  empty reason rejected")
    # The real list must parse.
    runner.load_expected_failures()
    print("ok  spec/expected_failures.toml parses")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
