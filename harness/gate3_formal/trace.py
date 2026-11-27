"""Turn a riscv-formal counterexample (yosys-smtbmc VCD) into a readable retirement
trace: one line per step with the instruction word fetched, and the RVFI record
(disassembled) whenever rvfi_valid is high.

Values are read from the `rvfi_testbench.wrapper` scope, which is the core's RVFI
port exactly as the checker sees it. smtbmc writes step k at time 10*k.
"""

from pathlib import Path

SCOPE = ("rvfi_testbench", "wrapper")
SIGNALS = ("reset", "ibus_addr", "ibus_rdata", "rvfi_valid", "rvfi_order", "rvfi_insn",
           "rvfi_pc_rdata", "rvfi_pc_wdata", "rvfi_rs1_addr", "rvfi_rs1_rdata",
           "rvfi_rs2_addr", "rvfi_rs2_rdata", "rvfi_rd_addr", "rvfi_rd_wdata")


def parse_vcd(path):
    """{signal: {step: int-or-None}} for SIGNALS in SCOPE (None for x/z bits)."""
    ids, scope, changes = {}, [], {}
    time = 0
    with open(path) as f:
        header = True
        for line in f:
            tok = line.split()
            if not tok:
                continue
            if header:
                if tok[0] == "$scope":
                    scope.append(tok[2])
                elif tok[0] == "$upscope":
                    scope.pop()
                elif tok[0] == "$var" and tuple(scope) == SCOPE and tok[4] in SIGNALS:
                    ids[tok[3]] = tok[4]
                elif tok[0] == "$enddefinitions":
                    header = False
                continue
            if tok[0].startswith("#"):
                time = int(tok[0][1:])
                continue
            if tok[0][0] in "bB" and len(tok) == 2:
                val, ident = tok[0][1:], tok[1]
            elif tok[0][0] in "01xXzZ":
                val, ident = tok[0][0], tok[0][1:]
            else:
                continue
            if ident in ids and time % 10 == 0:
                v = None if any(c in "xXzZ" for c in val) else int(val, 2)
                changes.setdefault(ids[ident], {})[time // 10] = v
    return changes


def _sx(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def disasm(w):
    """Minimal RV32I disassembler (enough to read a counterexample)."""
    if w is None:
        return "?"
    op, rd, f3, rs1, rs2, f7 = w & 0x7F, (w >> 7) & 31, (w >> 12) & 7, (w >> 15) & 31, (w >> 20) & 31, w >> 25
    i_imm = _sx(w >> 20, 12)
    s_imm = _sx(((w >> 25) << 5) | ((w >> 7) & 31), 12)
    b_imm = _sx((((w >> 31) & 1) << 12) | (((w >> 7) & 1) << 11) | (((w >> 25) & 63) << 5) | (((w >> 8) & 15) << 1), 13)
    j_imm = _sx((((w >> 31) & 1) << 20) | (((w >> 12) & 255) << 12) | (((w >> 20) & 1) << 11) | (((w >> 21) & 1023) << 1), 21)
    x = lambda r: f"x{r}"  # noqa: E731
    if op == 0x37:
        return f"lui {x(rd)}, {w >> 12:#x}"
    if op == 0x17:
        return f"auipc {x(rd)}, {w >> 12:#x}"
    if op == 0x6F:
        return f"jal {x(rd)}, {j_imm:+d}"
    if op == 0x67 and f3 == 0:
        return f"jalr {x(rd)}, {i_imm}({x(rs1)})"
    if op == 0x63 and f3 in (0, 1, 4, 5, 6, 7):
        return f"{['beq', 'bne', '', '', 'blt', 'bge', 'bltu', 'bgeu'][f3]} {x(rs1)}, {x(rs2)}, {b_imm:+d}"
    if op == 0x03 and f3 in (0, 1, 2, 4, 5):
        return f"{['lb', 'lh', 'lw', '', 'lbu', 'lhu'][f3]} {x(rd)}, {i_imm}({x(rs1)})"
    if op == 0x23 and f3 in (0, 1, 2):
        return f"{['sb', 'sh', 'sw'][f3]} {x(rs2)}, {s_imm}({x(rs1)})"
    if op == 0x13:
        if f3 == 1 and f7 == 0:
            return f"slli {x(rd)}, {x(rs1)}, {rs2}"
        if f3 == 5 and f7 in (0, 0x20):
            return f"{'srai' if f7 else 'srli'} {x(rd)}, {x(rs1)}, {rs2}"
        if f3 not in (1, 5):
            return f"{['addi', '', 'slti', 'sltiu', 'xori', '', 'ori', 'andi'][f3]} {x(rd)}, {x(rs1)}, {i_imm}"
    if op == 0x33 and f7 in (0, 0x20):
        names = {(0, 0): "add", (0x20, 0): "sub", (0, 1): "sll", (0, 2): "slt", (0, 3): "sltu",
                 (0, 4): "xor", (0, 5): "srl", (0x20, 5): "sra", (0, 6): "or", (0, 7): "and"}
        if (f7, f3) in names:
            return f"{names[f7, f3]} {x(rd)}, {x(rs1)}, {x(rs2)}"
    if op == 0x0F:
        return "fence"
    if w == 0x00000073:
        return "ecall"
    if w == 0x00100073:
        return "ebreak"
    return "(not RV32I)"


def render(vcd_path):
    sig = parse_vcd(vcd_path)
    steps = max((max(d) for d in sig.values() if d), default=-1) + 1

    def at(name, k):
        d, v = sig.get(name, {}), None
        for s in range(k, -1, -1):
            if s in d:
                return d[s]
        return v

    def h(v, w=8):
        return "x" * w if v is None else f"{v:0{w}x}"

    lines = ["step rst ibus_addr ibus_rdata | RVFI retirement (order pc_rdata insn  disasm ; "
             "rs1=val rs2=val -> rd=wdata ; pc_wdata)"]
    for k in range(steps):
        line = (f"{k:4} {at('reset', k) or 0:3} {h(at('ibus_addr', k))}  {h(at('ibus_rdata', k))}   |")
        if at("rvfi_valid", k):
            insn = at("rvfi_insn", k)
            line += (f" #{at('rvfi_order', k)} pc={h(at('rvfi_pc_rdata', k))} {h(insn)}  {disasm(insn):24} ;"
                     f" x{at('rvfi_rs1_addr', k)}={h(at('rvfi_rs1_rdata', k))}"
                     f" x{at('rvfi_rs2_addr', k)}={h(at('rvfi_rs2_rdata', k))}"
                     f" -> x{at('rvfi_rd_addr', k)}={h(at('rvfi_rd_wdata', k))}"
                     f" ; next={h(at('rvfi_pc_wdata', k))}")
        lines.append(line)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    sys.stdout.write(render(Path(sys.argv[1])))
