# Amarino RV32: how to work on this repo

This repo is an RV32I core in Amaranth HDL (`amarino/`) plus a verification harness
(`harness/`). **The harness is the only judge of whether the core is correct.** You
change the core; the harness tells you whether the change is right.

## The loop

```bash
. .venv/bin/activate                 # Amaranth 0.5.10, see harness/requirements.txt
tools/setup_harness_tools.sh         # once: pinned tools into .tools/
python -m harness                    # fast mode, a few minutes at most: run after every change
python -m harness --full             # the judge: run before saying a change is done
python -m harness --gate 2 -v        # one gate, listing passing checks too
python -m harness.gate2_cosim rand alu 3   # reproduce one co-simulation check
```

A run is **GREEN** only if every check passes or is a listed expected failure. Read
`harness/README.md` for the gates and the verdicts. In short:

- **FAIL:** your change broke something.
- **XPASS:** your change fixed a listed bug. Tell the user; a human removes the entry.
- **ERROR:** a tool is missing or the harness crashed. That is never success.

Logs, counterexamples and traces go to `harness-out/<timestamp>/`. Every failing check
names the log that explains it.

## Rules

1. **Only change files under `amarino/`** (and `tests/`, `README.md`, `docs/` other than
   `docs/rvfi.md`). Everything else listed below is protected.
2. **Never edit the harness or its spec to get green.** That means `harness/` (including
   `harness/spec/expected_failures.toml`, `harness/spec/platform.toml` and
   `harness/mutation/mutants.toml`), `docs/rvfi.md`, `amarino/rvfi.py`,
   `tools/setup_harness_tools.sh`, `.github/`, `.claude/` and this file. Edits there are
   denied for agents (`.claude/settings.json`) and need the owner's review (CODEOWNERS).
   If you think the harness is wrong, stop and explain why to the user, with the evidence.
3. **Keep the RVFI port truthful.** `Top` wires the core's signals into
   `amarino/rvfi.py` (see `docs/rvfi.md`). When you restructure the pipeline you may need
   to rewire those taps in `amarino/top.py`. Tap the signals that really carry the
   instruction's effects, and never feed RVFI from anything that predicts or models the
   right answer. Call out any change to the taps explicitly to the user.
4. **"Done" means `python -m harness --full` is GREEN, and you report exactly which
   expected failures turned into XPASS.** Don't claim a fix the harness didn't confirm.
5. Don't skip, disable or weaken checks, and don't reduce formal depths. Don't make
   programs or tests avoid a bug. The expected-failures list is the only place known
   bugs are acknowledged, and only humans edit it.

## Core facts worth knowing

- **Pipeline:** effectively IF → ID → EX/WB. The retire latch is combinational, and the
  register-file write happens at the end of EX.
- **PCs:** the core's PCs are word indices. RVFI reports byte addresses (`word * 4`).
- **Platform** (`harness/spec/platform.toml`): programs are linked at `0x80000000`, and
  the core starts at word 1 (reset PC `0x80000004`).
- **Instruction memory:** `Top(imem="external")` exposes the instruction bus for
  simulation and formal; `Top()` is the FPGA build.
- **Known bugs:** the current ones are exactly the entries in
  `harness/spec/expected_failures.toml`, with their reasons.

## Harness validation

`python -m harness.mutation` runs the gates on deliberately broken copies of the core
(`harness/mutation/mutants.toml`) and shows that each is caught. CI runs it nightly,
with the full gates (`.github/workflows/harness-nightly.yml`). If you change the core,
some mutants may stop applying. That is reported, and the mutants are updated by a
human.
