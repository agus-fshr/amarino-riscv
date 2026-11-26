"""Just enough RV32I decoding for gate 2: mnemonics for logs, which registers an
encoding reads (RVFI rs*_addr), and the width of loads and stores.

This is a description of the ISA (unprivileged spec 20191213, chapter 2), not of
the core. Anything outside RV32I decodes as "unknown".
"""

OP_LUI, OP_AUIPC, OP_JAL, OP_JALR = 0x37, 0x17, 0x6F, 0x67
OP_BRANCH, OP_LOAD, OP_STORE = 0x63, 0x03, 0x23
OP_IMM, OP_OP, OP_MISC_MEM, OP_SYSTEM = 0x13, 0x33, 0x0F, 0x73

_BRANCH = {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}
_LOAD = {0: "lb", 1: "lh", 2: "lw", 4: "lbu", 5: "lhu"}
_STORE = {0: "sb", 1: "sh", 2: "sw"}
_IMM = {0: "addi", 2: "slti", 3: "sltiu", 4: "xori", 6: "ori", 7: "andi"}
_OP = {(0, 0): "add", (0, 0x20): "sub", (1, 0): "sll", (2, 0): "slt", (3, 0): "sltu",
       (4, 0): "xor", (5, 0): "srl", (5, 0x20): "sra", (6, 0): "or", (7, 0): "and"}

# Load/store width in bytes by funct3.
MEM_WIDTH = {0: 1, 1: 2, 2: 4, 4: 1, 5: 2}


def sext(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def fields(insn):
    return dict(opcode=insn & 0x7F, rd=(insn >> 7) & 31, funct3=(insn >> 12) & 7,
                rs1=(insn >> 15) & 31, rs2=(insn >> 20) & 31, funct7=insn >> 25)


def imm_i(insn):
    return sext(insn >> 20, 12)


def imm_s(insn):
    return sext(((insn >> 25) << 5) | ((insn >> 7) & 31), 12)


def imm_b(insn):
    v = (((insn >> 31) & 1) << 12) | (((insn >> 7) & 1) << 11) | (((insn >> 25) & 0x3F) << 5) \
        | (((insn >> 8) & 0xF) << 1)
    return sext(v, 13)


def imm_j(insn):
    v = (((insn >> 31) & 1) << 20) | (((insn >> 12) & 0xFF) << 12) | (((insn >> 20) & 1) << 11) \
        | (((insn >> 21) & 0x3FF) << 1)
    return sext(v, 21)


def mnemonic(insn):
    f = fields(insn)
    op, f3, f7 = f["opcode"], f["funct3"], f["funct7"]
    if insn & 3 != 3:
        return "unknown"
    if op == OP_LUI:
        return "lui"
    if op == OP_AUIPC:
        return "auipc"
    if op == OP_JAL:
        return "jal"
    if op == OP_JALR and f3 == 0:
        return "jalr"
    if op == OP_BRANCH and f3 in _BRANCH:
        return _BRANCH[f3]
    if op == OP_LOAD and f3 in _LOAD:
        return _LOAD[f3]
    if op == OP_STORE and f3 in _STORE:
        return _STORE[f3]
    if op == OP_IMM:
        if f3 == 1 and f7 == 0:
            return "slli"
        if f3 == 5 and f7 in (0, 0x20):
            return "srai" if f7 else "srli"
        if f3 in _IMM:
            return _IMM[f3]
    if op == OP_OP and (f3, f7) in _OP:
        return _OP[(f3, f7)]
    if op == OP_MISC_MEM and f3 == 0:
        return "fence"
    if insn == 0x00000073:
        return "ecall"
    if insn == 0x00100073:
        return "ebreak"
    return "unknown"


def reads(insn):
    """(reads rs1, reads rs2) for a valid RV32I encoding, per the opcode."""
    op = insn & 0x7F
    known = mnemonic(insn) != "unknown"
    rs1 = known and op in (OP_IMM, OP_OP, OP_LOAD, OP_STORE, OP_BRANCH, OP_JALR)
    rs2 = known and op in (OP_OP, OP_STORE, OP_BRANCH)
    return rs1, rs2


def disasm(insn):
    m = mnemonic(insn)
    f = fields(insn)
    rd, rs1, rs2 = (f"x{f[k]}" for k in ("rd", "rs1", "rs2"))
    op = f["opcode"]
    if m == "unknown":
        return f".word 0x{insn:08x}"
    if m in ("lui", "auipc"):
        return f"{m} {rd}, 0x{insn >> 12:x}"
    if m == "jal":
        return f"jal {rd}, {imm_j(insn):+d}"
    if m == "jalr" or op == OP_LOAD:
        return f"{m} {rd}, {imm_i(insn)}({rs1})"
    if op == OP_BRANCH:
        return f"{m} {rs1}, {rs2}, {imm_b(insn):+d}"
    if op == OP_STORE:
        return f"{m} {rs2}, {imm_s(insn)}({rs1})"
    if m in ("slli", "srli", "srai"):
        return f"{m} {rd}, {rs1}, {f['rs2']}"
    if op == OP_IMM:
        return f"{m} {rd}, {rs1}, {imm_i(insn)}"
    if op == OP_OP:
        return f"{m} {rd}, {rs1}, {rs2}"
    return m
