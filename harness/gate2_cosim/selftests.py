"""Gate 2's own self-checks (g2.selftest.*), so the gate cannot pass by being blind.

  sail-isa            Sail with our override reports plain rv32i
  sail-parse          a hand-written program parses into exactly the records worked out
                      by hand below (values, RVFI byte lanes, x0, unchanged writes, trap)
  sail-no-c           a jalr to a 2-byte-aligned target traps (C really is off)
  from-core           core RVFI records are relocated to the image base
  compare.identity    identical traces compare equal
  compare.<field>     a copy of a reference trace with only <field> changed in one
                      record is reported at that record and field
  compare.missing     a truncated core trace is reported as missing records
  compare.dontcare    data in byte lanes outside the access mask is ignored (RVFI)
  randgen             programs are a pure function of (seed, profile, index) and each
                      profile contains the instructions it is meant to test
"""

import re
import subprocess

from harness.common.coresim import Retire
from harness.common.program import IMAGE_BASE
from harness.common.tools import tool
from harness.gate2_cosim import compare, programs, randgen, sail
from harness.gate2_cosim.compare import Rec
from harness.runner import Check

PARSE_PROGRAM = """
.section .text.init
.option norvc
.global _start
_start:
  addi x1, x0, 5          # 80000004
  lui  x2, 0x80001        # 80000008
  sw   x1, 0(x2)          # 8000000c
  sb   x1, 3(x2)          # 80000010
  sh   x1, 2(x2)          # 80000014
  lw   x3, 0(x2)          # 80000018
  lbu  x4, 3(x2)          # 8000001c
  lh   x5, 2(x2)          # 80000020
  addi x6, x0, -1         # 80000024
  sb   x6, 1(x2)          # 80000028
  lb   x7, 1(x2)          # 8000002c
  lhu  x8, 0(x2)          # 80000030
  beq  x1, x5, 1f         # 80000034
  addi x9, x0, 1          # 80000038 (skipped)
1: jal x10, 2f            # 8000003c
  addi x9, x0, 2          # 80000040 (skipped)
2: auipc x11, 1           # 80000044
  addi x1, x1, 0          # 80000048 rewrites the same value
  add  x0, x1, x1         # 8000004c writes x0: no rd
  ecall                   # 80000050 traps; mtvec resets to 0
.global halt
halt: j halt
"""

# Worked out by hand from the RV32I spec. Memory: word at 0x80001000 is
# 05 00 00 00 after sw, 05 00 00 05 after sb +3, 05 00 05 00 after sh +2,
# 05 ff 05 00 after sb +1.
B = 0x80000000
PARSE_EXPECTED = [
    Rec(0, B + 0x04, 0x00500093, rd_addr=1, rd_wdata=5),
    Rec(1, B + 0x08, 0x80001137, rd_addr=2, rd_wdata=0x80001000),
    Rec(2, B + 0x0c, 0x00112023, rs1_addr=2, rs1_rdata=0x80001000, rs2_addr=1, rs2_rdata=5,
        mem_addr=0x80001000, mem_wmask=0xF, mem_wdata=0x00000005),
    Rec(3, B + 0x10, 0x001101A3, rs1_addr=2, rs1_rdata=0x80001000, rs2_addr=1, rs2_rdata=5,
        mem_addr=0x80001000, mem_wmask=0x8, mem_wdata=0x05000000),
    Rec(4, B + 0x14, 0x00111123, rs1_addr=2, rs1_rdata=0x80001000, rs2_addr=1, rs2_rdata=5,
        mem_addr=0x80001000, mem_wmask=0xC, mem_wdata=0x00050000),
    Rec(5, B + 0x18, 0x00012183, rs1_addr=2, rs1_rdata=0x80001000, rd_addr=3, rd_wdata=0x00050005,
        mem_addr=0x80001000, mem_rmask=0xF, mem_rdata=0x00050005),
    Rec(6, B + 0x1c, 0x00314203, rs1_addr=2, rs1_rdata=0x80001000, rd_addr=4, rd_wdata=0,
        mem_addr=0x80001000, mem_rmask=0x8, mem_rdata=0),
    Rec(7, B + 0x20, 0x00211283, rs1_addr=2, rs1_rdata=0x80001000, rd_addr=5, rd_wdata=5,
        mem_addr=0x80001000, mem_rmask=0xC, mem_rdata=0x00050000),
    Rec(8, B + 0x24, 0xFFF00313, rd_addr=6, rd_wdata=0xFFFFFFFF),
    Rec(9, B + 0x28, 0x006100A3, rs1_addr=2, rs1_rdata=0x80001000, rs2_addr=6, rs2_rdata=0xFFFFFFFF,
        mem_addr=0x80001000, mem_wmask=0x2, mem_wdata=0x0000FF00),
    Rec(10, B + 0x2c, 0x00110383, rs1_addr=2, rs1_rdata=0x80001000, rd_addr=7, rd_wdata=0xFFFFFFFF,
        mem_addr=0x80001000, mem_rmask=0x2, mem_rdata=0x0000FF00),
    Rec(11, B + 0x30, 0x00015403, rs1_addr=2, rs1_rdata=0x80001000, rd_addr=8, rd_wdata=0x0000FF05,
        mem_addr=0x80001000, mem_rmask=0x3, mem_rdata=0x0000FF05),
    Rec(12, B + 0x34, 0x00508463, rs1_addr=1, rs1_rdata=5, rs2_addr=5, rs2_rdata=5),
    Rec(13, B + 0x3c, 0x0080056F, rd_addr=10, rd_wdata=B + 0x40),
    Rec(14, B + 0x44, 0x00001597, rd_addr=11, rd_wdata=B + 0x1044),
    Rec(15, B + 0x48, 0x00008093, rs1_addr=1, rs1_rdata=5, rd_addr=1, rd_wdata=5),
    Rec(16, B + 0x4c, 0x00108033, rs1_addr=1, rs1_rdata=5, rs2_addr=1, rs2_rdata=5),
    Rec(17, B + 0x50, 0x00000073, trap=1),
]
# pc_wdata: the next record's pc; the trap goes to mtvec's reset value, 0.
for _a, _b in zip(PARSE_EXPECTED, PARSE_EXPECTED[1:]):
    _a.pc_wdata = _b.pc_rdata
PARSE_EXPECTED[-1].pc_wdata = 0

NO_C_PROGRAM = """
.section .text.init
.option norvc
.global _start
_start:
  lui  x5, %hi(1f)
  nop
  nop
  nop
  addi x5, x5, %lo(1f)
  jalr x0, 2(x5)
1: nop
  nop
.global halt
halt: j halt
"""


def _same(a, b):
    return [compare.first_difference(x, y) for x, y in zip(a, b)]


def check_isa():
    r = subprocess.run([tool("sail_riscv_sim"), "--rv32", "--config-override", str(sail.CONFIG),
                        "--print-isa-string"], capture_output=True, text=True)
    isa = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    # Sail prints the Zvl* (minimum VLEN) names even with V disabled; they add no instructions.
    ok = r.returncode == 0 and re.fullmatch(r"rv32i(_zvl\d+b)*", isa) is not None
    return Check("g2.selftest.sail-isa", ok, f"isa string {isa!r}\n{r.stderr[-500:]}")


def check_parse(ctx):
    elf, img = programs.build_asm(PARSE_PROGRAM, ctx.path("gate2", "selftest", "parse", "x").parent)
    trace = sail.run(elf, img.symbols["halt"], ctx.path("gate2", "selftest", "parse", "x").parent)
    got = trace.records
    problems = []
    if trace.ended != "trap":
        problems.append(f"ended {trace.ended}, expected trap")
    if len(got) != len(PARSE_EXPECTED):
        problems.append(f"{len(got)} records, expected {len(PARSE_EXPECTED)}")
    for i, (e, g) in enumerate(zip(PARSE_EXPECTED, got)):
        f = compare.first_difference(e, g)
        # first_difference ignores data outside the masks; the parser must also leave it 0.
        if f is None and (g.mem_rdata & ~compare.lane_mask(g.mem_rmask) or
                          g.mem_wdata & ~compare.lane_mask(g.mem_wmask)):
            f = "mem data outside mask"
        if f is not None:
            problems.append(f"record {i}: {f}: expected {compare.fmt(e)}\n   got {compare.fmt(g)}")
    if got and got[-1].note != "m-call":
        problems.append(f"trap reported as {got[-1].note!r}, expected m-call")
    return Check("g2.selftest.sail-parse", not problems, "\n".join(problems) or "all records as expected")


def check_no_c(ctx):
    d = ctx.path("gate2", "selftest", "no-c", "x").parent
    elf, img = programs.build_asm(NO_C_PROGRAM, d)
    trace = sail.run(elf, img.symbols["halt"], d)
    last = trace.records[-1]
    ok = (trace.ended == "trap" and len(trace.records) == 6 and last.insn == 0x00228067
          and "misaligned" in last.note)
    return Check("g2.selftest.sail-no-c", ok,
                 f"jalr to a 2-byte-aligned target: ended {trace.ended}, last {compare.fmt(last)}")


def check_from_core():
    r = Retire(order=3, insn=0x13, pc_rdata=0x4, pc_wdata=0x8, rs1_addr=1, rs1_rdata=2, rs2_addr=3,
               rs2_rdata=4, rd_addr=5, rd_wdata=6, trap=0, mem_addr=0x80001000, mem_rmask=1,
               mem_wmask=0, mem_rdata=7, mem_wdata=0)
    c = compare.from_core(r)
    want = Rec(3, IMAGE_BASE + 4, 0x13, 0, 1, 2, 3, 4, 5, 6, 1, 0, 0x80001000, 7, 0, IMAGE_BASE + 8)
    f = compare.first_difference(want, c)
    return Check("g2.selftest.from-core", f is None and c.mem_rdata == 7,
                 f"converted {compare.fmt(c)}; first difference {f}")


def compare_checks():
    ref = [Rec(**{k: getattr(r, k) for k in Rec.__dataclass_fields__}) for r in PARSE_EXPECTED]
    out = []
    res = compare.compare(ref, list(ref))
    out.append(Check("g2.selftest.compare.identity", res.ok and res.matched == len(ref),
                     f"identical traces: {res.data()}"))
    for field in compare.FIELDS:
        # first record where changing this field is meaningful
        if field in ("mem_rdata", "mem_rmask"):
            i = next(k for k, r in enumerate(ref) if r.mem_rmask and k > 0)
        elif field in ("mem_wdata", "mem_wmask", "mem_addr"):
            i = next(k for k, r in enumerate(ref) if r.mem_wmask and k > 0)
        else:
            i = 4
        dut = list(ref)
        dut[i] = compare.perturb(ref[i], field)
        res = compare.compare(ref, dut)
        ok = res.field == field and res.matched == i and not res.ok
        out.append(Check(f"g2.selftest.compare.{field}", ok,
                         f"perturbed {field} of record {i}: got {res.data()}"))
    res = compare.compare(ref, ref[:7])
    out.append(Check("g2.selftest.compare.missing", res.field == "missing" and res.matched == 7,
                     f"truncated to 7 of {len(ref)}: got {res.data()}"))
    # A core reporting a memory access the reference does not have is a divergence, too.
    dut = list(ref)
    dut[0] = compare.perturb(ref[0], "mem_rmask")
    res = compare.compare(ref, dut)
    extra = res.field == "mem_rmask" and res.matched == 0
    # Bytes outside the access mask are don't-care in RVFI.
    i = next(k for k, r in enumerate(ref) if r.mem_rmask and r.mem_rmask != 0xF)
    dut = list(ref)
    d = Rec(**{k: getattr(ref[i], k) for k in Rec.__dataclass_fields__})
    d.mem_rdata |= compare.lane_mask(0xF & ~ref[i].mem_rmask)
    dut[i] = d
    res2 = compare.compare(ref, dut)
    out.append(Check("g2.selftest.compare.dontcare", extra and res2.ok,
                     f"spurious access flagged: {extra}; lanes outside mask ignored: {res2.data()}"))
    return out


def check_randgen(seed):
    problems = []
    want = {"alu": r"\b(add|sub|xor|addi|andi|sltiu)\b", "shift": r"\b(sll|srl|sra|slli|srli|srai)\b",
            "upper": r"\b(lui|auipc) x\d+, 0x", "branch": r"\b(beq|bne|blt|bge|bltu|bgeu)\b",
            "jump": r"\bjalr x\d+, -?\d+\(x", "mem": r"\b(lb|lh|lw|lbu|lhu|sb|sh|sw) x\d+, -?\d+\(x",
            "full": r"\b(srai|auipc|sw|bne|jalr|lbu)\b"}
    forbidden = {"alu": r"\b(sll|srl|sra|slli|srli|srai|auipc|jal|jalr|beq|bne|lw|sw)\b"}
    for p in randgen.PROFILES:
        a, b = randgen.generate(seed, p, 0), randgen.generate(seed, p, 0)
        if a != b:
            problems.append(f"{p}: same (seed, profile, index) gave different programs")
        if a == randgen.generate(seed, p, 1):
            problems.append(f"{p}: index 0 and 1 gave the same program")
        if a == randgen.generate(seed + 1, p, 0):
            problems.append(f"{p}: seed and seed+1 gave the same program")
        body = a.split("# body", 1)[1]
        if not re.search(want[p], body):
            problems.append(f"{p}: body lacks {want[p]}")
        if p in forbidden and re.search(forbidden[p], body.split("halt:")[0]):
            problems.append(f"{p}: body contains {forbidden[p]}")
    return Check("g2.selftest.randgen", not problems, "\n".join(problems) or "deterministic, profiles as specified")


def checks(ctx):
    from harness.runner import guarded
    out = [guarded("g2.selftest.sail-isa", check_isa),
           guarded("g2.selftest.sail-parse", check_parse, ctx),
           guarded("g2.selftest.sail-no-c", check_no_c, ctx),
           guarded("g2.selftest.from-core", check_from_core),
           guarded("g2.selftest.randgen", check_randgen, ctx.seed)]
    cmp = guarded("g2.selftest.compare", compare_checks)
    out += cmp if isinstance(cmp, list) else [cmp]
    return out
