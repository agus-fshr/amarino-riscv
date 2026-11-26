"""Normalised retirement records and the field-by-field trace comparator.

Both sides are turned into `Rec`s with RVFI conventions (docs/rvfi.md, riscv-formal
with aligned memory):

  pc_rdata, pc_wdata   architectural byte addresses (the core's RVFI PCs are relative
                       to image base 0, so IMAGE_BASE is added; spec/platform.toml)
  rs1/rs2_addr, _rdata the registers the encoding reads (0/0 when it reads none)
  rd_addr, rd_wdata    the register written (0/0 when none, or when rd is x0)
  mem_addr             word-aligned address of a load/store, mem_rmask/mem_wmask the
                       bytes accessed in that word, mem_rdata/mem_wdata the data in
                       those byte lanes (other lanes are don't-care, as in RVFI)
  trap                 1 if the instruction raised an exception

Records are compared in order; within a record, fields are compared in FIELDS order
(memory fields only when either side accessed memory). The first difference ends the
comparison. A core trace shorter than the reference is a divergence at the first
missing record (field "missing").
"""

from dataclasses import dataclass, asdict, fields as dc_fields

from harness.common.program import IMAGE_BASE
from harness.gate2_cosim import rv32i

MASK32 = 0xFFFF_FFFF

# Comparison order within a record. `trap` comes right after the instruction because
# the other fields of a trapping instruction are not meaningful; pc_wdata comes last so
# a wrong result of a jump is reported as rd_wdata and a wrong target as pc_wdata.
FIELDS = ("order", "pc_rdata", "insn", "trap", "rs1_addr", "rs1_rdata", "rs2_addr", "rs2_rdata",
          "rd_addr", "rd_wdata", "mem_rmask", "mem_wmask", "mem_addr", "mem_rdata", "mem_wdata",
          "pc_wdata")
MEM_FIELDS = ("mem_rmask", "mem_wmask", "mem_addr", "mem_rdata", "mem_wdata")


@dataclass
class Rec:
    order: int
    pc_rdata: int
    insn: int
    trap: int = 0
    rs1_addr: int = 0
    rs1_rdata: int = 0
    rs2_addr: int = 0
    rs2_rdata: int = 0
    rd_addr: int = 0
    rd_wdata: int = 0
    mem_rmask: int = 0
    mem_wmask: int = 0
    mem_addr: int = 0
    mem_rdata: int = 0
    mem_wdata: int = 0
    pc_wdata: int = 0
    note: str = ""        # reference only: e.g. the exception name; never compared


def from_core(r, order=None):
    """coresim.Retire -> Rec. PCs are relocated to the platform's image base."""
    return Rec(order=r.order if order is None else order,
               pc_rdata=(r.pc_rdata + IMAGE_BASE) & MASK32, insn=r.insn & MASK32, trap=r.trap,
               rs1_addr=r.rs1_addr, rs1_rdata=r.rs1_rdata, rs2_addr=r.rs2_addr, rs2_rdata=r.rs2_rdata,
               rd_addr=r.rd_addr, rd_wdata=r.rd_wdata, mem_rmask=r.mem_rmask, mem_wmask=r.mem_wmask,
               mem_addr=r.mem_addr, mem_rdata=r.mem_rdata, mem_wdata=r.mem_wdata,
               pc_wdata=(r.pc_wdata + IMAGE_BASE) & MASK32)


def lane_mask(bytemask):
    return sum(0xFF << (8 * i) for i in range(4) if bytemask >> i & 1)


def first_difference(ref, dut):
    """Name of the first field (FIELDS order) where two records differ, or None."""
    for f in FIELDS:
        if f in MEM_FIELDS:
            continue
        if f == "pc_wdata":
            if ref.mem_rmask or ref.mem_wmask or dut.mem_rmask or dut.mem_wmask:
                for m in MEM_FIELDS:
                    a, b = getattr(ref, m), getattr(dut, m)
                    if m == "mem_rdata":
                        a, b = a & lane_mask(ref.mem_rmask), b & lane_mask(ref.mem_rmask)
                    elif m == "mem_wdata":
                        a, b = a & lane_mask(ref.mem_wmask), b & lane_mask(ref.mem_wmask)
                    if a != b:
                        return m
        if getattr(ref, f) != getattr(dut, f):
            return f
    return None


@dataclass
class Result:
    matched: int          # identical leading records
    total: int            # reference length
    field: str | None     # first mismatching field, None if the traces agree

    @property
    def ok(self):
        return self.field is None

    def data(self):
        return {"matched": self.matched, "total": self.total, "field": self.field}


def compare(ref, dut):
    for i, r in enumerate(ref):
        if i >= len(dut):
            return Result(i, len(ref), "missing")
        f = first_difference(r, dut[i])
        if f is not None:
            return Result(i, len(ref), f)
    return Result(len(ref), len(ref), None)


def fmt(rec):
    if rec is None:
        return "-"
    s = (f"#{rec.order:<5} pc={rec.pc_rdata:08x} {rec.insn:08x} {rv32i.disasm(rec.insn):<24}"
         f" rs1=x{rec.rs1_addr}:{rec.rs1_rdata:08x} rs2=x{rec.rs2_addr}:{rec.rs2_rdata:08x}"
         f" rd=x{rec.rd_addr}:{rec.rd_wdata:08x}")
    if rec.mem_rmask or rec.mem_wmask:
        s += (f" mem[{rec.mem_addr:08x}] r{rec.mem_rmask:x}:{rec.mem_rdata:08x}"
              f" w{rec.mem_wmask:x}:{rec.mem_wdata:08x}")
    if rec.trap:
        s += f" TRAP{('(' + rec.note + ')') if rec.note else ''}"
    return s + f" next={rec.pc_wdata:08x}"


def report(result, ref, dut, context=8):
    """Human-readable divergence log: the last `context` records before the divergence
    and the diverging record, reference above core."""
    if result.ok:
        return f"traces agree on all {result.total} records\n"
    i = result.matched
    lines = [f"first divergence at record {i} of {result.total}, field {result.field}"]
    if result.field == "missing":
        lines.append(f"core retired only {len(dut)} of {result.total} reference instructions")
    else:
        r, d = ref[i], dut[i]
        lines.append(f"  reference {result.field} = {getattr(r, result.field):#x}"
                     f"   core {result.field} = {getattr(d, result.field):#x}")
    lines.append("")
    for k in range(max(0, i - context), min(i + 1, len(ref))):
        mark = ">>" if k == i else "  "
        lines.append(f"{mark} ref  {fmt(ref[k])}")
        lines.append(f"{mark} core {fmt(dut[k] if k < len(dut) else None)}")
    return "\n".join(lines) + "\n"


def perturb(rec, field):
    """A copy of `rec` with one field changed so it no longer matches (comparator self-test)."""
    d = asdict(rec)
    if field in ("mem_rdata", "mem_wdata"):
        mask = rec.mem_rmask if field == "mem_rdata" else rec.mem_wmask
        lane = next(i for i in range(4) if mask >> i & 1)
        d[field] ^= 1 << (8 * lane)
    elif field in ("rs1_addr", "rs2_addr", "rd_addr"):
        d[field] = (d[field] + 1) % 32
    elif field == "trap":
        d[field] ^= 1
    elif field in ("mem_rmask", "mem_wmask"):
        d[field] ^= 0x8 if d[field] != 0x8 else 0x1
    else:
        d[field] ^= 0x4
    return Rec(**{f.name: d[f.name] for f in dc_fields(Rec)})
