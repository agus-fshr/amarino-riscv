"""Random RV32I instruction-sequence generator for co-simulation.

generate(seed, profile, index) -> assembly text. The program is a pure function of
(seed, profile, index): all randomness comes from random.Random(f"{seed}:{profile}:{index}").

Every program:
  - starts at _start (the reset PC), initialises a small pool of registers to
    interesting values (lui group, then addi group, so no initialising instruction
    depends on one less than 4 instructions back; the init therefore does not itself
    trigger read-after-write hazards and every profile reaches its own body),
  - runs a body of the profile's instructions over that pool, reading registers
    written 1..3 instructions earlier most of the time (dense RAW hazards), with x0
    as both source and destination and edge-case immediates,
  - ends in the self-loop `halt: j halt`.
All control flow is structured, so termination is guaranteed: forward branches and
jumps skip over a block; backward branches/jumps close loops counted down by a
reserved register (at most 3 iterations, nesting depth 2); subroutines (after halt)
return through a link register they never overwrite. Every target is 4-byte aligned.
Loads and stores are naturally aligned and stay inside a 4 KiB data region.

Profiles (one check slice each):
  alu    OP and OP-IMM, no shifts
  shift  sll/srl/sra/slli/srli/srai (+ alu to vary the values)
  upper  lui/auipc (+ alu consumers)
  branch all six branches, forward skips and counted backward loops (+ alu)
  jump   jal/jalr: forward skips, computed jalr targets (incl. bit 0 set), calls and
         returns, backward jal loops (+ alu)
  mem    lb/lh/lw/lbu/lhu/sb/sh/sw with fixed bases (incl. +-2047/-2048 offsets) and
         computed addresses (+ alu)
  full   all of the above mixed
"""

import random

PROFILES = ("alu", "shift", "upper", "branch", "jump", "mem", "full")

ALU_R = ("add", "sub", "slt", "sltu", "xor", "or", "and")
ALU_I = ("addi", "slti", "sltiu", "xori", "ori", "andi")
SHIFT_R = ("sll", "srl", "sra")
SHIFT_I = ("slli", "srli", "srai")
BRANCHES = ("beq", "bne", "blt", "bge", "bltu", "bgeu")
LOADS = (("lb", 1), ("lh", 2), ("lw", 4), ("lbu", 1), ("lhu", 2))
STORES = (("sb", 1), ("sh", 2), ("sw", 4))

INTERESTING = (0, 1, 2, 0xFFFFFFFF, 0x7FFFFFFF, 0x80000000, 0x80000001, 0x55555555, 0xAAAAAAAA,
               0x1F, 0x20, 0x21, 0x7FF, 0x800, 0xFFFFF800, 0xFFFFF7FF, 0x0000FFFF, 0xFFFF0000,
               0x000000FF, 0x00000080)
IMM12_EDGES = (0, 1, -1, 2047, -2048, -2047, 2046, 0x400, -0x400, 0x7F, 0x80, -0x80, 0x555, -0x556)
UIMM20_EDGES = (0, 1, 0xFFFFF, 0x80000, 0x7FFFF, 0x00800, 0xFFFFE)
SHAMT_EDGES = (0, 1, 2, 15, 16, 30, 31)

DATA_WORDS = 1024            # 4 KiB data region `gdata`
BODY_ITEMS = (60, 100)        # top-level items per program


class _Gen:
    def __init__(self, seed, profile, index):
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile!r}")
        self.rng = random.Random(f"{seed}:{profile}:{index}")
        self.profile = profile
        rng = self.rng
        regs = list(range(1, 32))
        rng.shuffle(regs)
        n = rng.choice((3, 4, 5))
        self.pool = regs[:n]                     # body sources/destinations
        self.counters = regs[n:n + 2]            # loop counters (never written by the body)
        self.bases = regs[n + 2:n + 4]           # mem bases: gdata + 2048, gdata
        self.recent = []                         # last destinations, most recent last
        self.labels = 0
        self.subs = []                           # subroutine bodies, emitted after halt
        self.noread = ()                         # registers src() must not pick for now
        self.out = []

    # ---- operand choice --------------------------------------------------------
    def label(self, kind):
        self.labels += 1
        return f".L{kind}{self.labels}"

    def src(self):
        r = self.rng.random()
        recent = [x for x in self.recent if x not in self.noread]
        pool = [x for x in self.pool if x not in self.noread]
        if r < 0.12 or not pool:
            return 0
        if r < 0.72 and recent:
            return self.rng.choice(recent)              # RAW at distance 1..3
        return self.rng.choice(pool)

    def dst(self, exclude=()):
        if self.rng.random() < 0.08:
            return 0
        choices = [r for r in self.pool if r not in exclude] or [0]
        rd = self.rng.choice(choices)
        return rd

    def wrote(self, rd):
        if rd:
            self.recent = (self.recent + [rd])[-3:]

    def imm12(self):
        if self.rng.random() < 0.5:
            return self.rng.choice(IMM12_EDGES)
        return self.rng.randint(-2048, 2047)

    def uimm20(self):
        if self.rng.random() < 0.5:
            return self.rng.choice(UIMM20_EDGES)
        return self.rng.randint(0, 0xFFFFF)

    def shamt(self):
        if self.rng.random() < 0.6:
            return self.rng.choice(SHAMT_EDGES)
        return self.rng.randint(0, 31)

    # ---- straight-line instructions --------------------------------------------
    def emit(self, line, rd=0):
        self.out.append("  " + line)
        self.wrote(rd)

    def alu(self, exclude=()):
        rd = self.dst(exclude)
        if self.rng.random() < 0.5:
            op = self.rng.choice(ALU_R)
            self.emit(f"{op} x{rd}, x{self.src()}, x{self.src()}", rd)
        else:
            op = self.rng.choice(ALU_I)
            self.emit(f"{op} x{rd}, x{self.src()}, {self.imm12()}", rd)

    def shift(self, exclude=()):
        rd = self.dst(exclude)
        # Shifting x0 always gives 0; re-pick once so values do not collapse to zero.
        a = self.src() or self.src()
        if self.rng.random() < 0.5:
            self.emit(f"{self.rng.choice(SHIFT_R)} x{rd}, x{a}, x{self.src()}", rd)
        else:
            self.emit(f"{self.rng.choice(SHIFT_I)} x{rd}, x{a}, {self.shamt()}", rd)

    def upper(self, exclude=()):
        rd = self.dst(exclude)
        self.emit(f"{self.rng.choice(('lui', 'auipc'))} x{rd}, {self.uimm20():#x}", rd)

    def mem(self, exclude=()):
        rng = self.rng
        store = rng.random() < 0.45
        op, w = rng.choice(STORES if store else LOADS)
        if rng.random() < 0.3:
            # computed address: t = (src & 0x7fc) + gdata, then a small aligned offset
            t = self.dst(tuple(exclude) + (0,))
            if t == 0:
                return self.alu(exclude)
            self.emit(f"andi x{t}, x{self.src()}, {0x7FC}", t)
            self.emit(f"add x{t}, x{t}, x{self.bases[1]}", t)
            base, off = t, rng.choice((0, w, 4 * rng.randint(0, 16), 2044 - (2044 % w)))
            off -= off % w
        else:
            base = self.bases[0]                 # gdata + 2048: whole range -2048..2047 is in gdata
            hi = 2047 - (2047 % w) if w > 1 else 2047
            off = rng.choice((-2048, hi, 0, -w, w, rng.randint(-2048, 2047)))
            off -= off % w
        if store:
            self.emit(f"{op} x{self.src()}, {off}(x{base})")
        else:
            rd = self.dst(exclude)
            self.emit(f"{op} x{rd}, {off}(x{base})", rd)

    def simple(self, kinds, exclude=()):
        getattr(self, self.rng.choice(kinds))(exclude)

    # ---- control flow -------------------------------------------------------------
    def fwd_branch(self, kinds, depth, exclude=()):
        lab = self.label("f")
        op = self.rng.choice(BRANCHES)
        a = self.src()
        b = a if self.rng.random() < 0.15 else self.src()
        self.emit(f"{op} x{a}, x{b}, {lab}")
        self.block(kinds, self.rng.randint(0, 4), depth + 1, exclude)
        self.out.append(f"{lab}:")

    def loop(self, kinds, depth, exclude=(), jump=False):
        if depth >= 2:
            return self.simple(kinds, exclude)
        c = self.counters[depth]
        n = self.rng.randint(1, 3)
        top, done = self.label("loop"), self.label("done")
        form = "jal" if jump else self.rng.choice(("bne", "blt", "bltu", "bge"))
        self.emit(f"addi x{c}, x0, {n - 1 if form == 'bge' else n}")
        self.out.append(f"{top}:")
        self.block(kinds, self.rng.randint(1, 5), depth + 1, tuple(exclude) + (c,))
        self.emit(f"addi x{c}, x{c}, -1")
        if form == "bne":
            self.emit(f"bne x{c}, x0, {top}")
        elif form == "blt":
            self.emit(f"blt x0, x{c}, {top}")
        elif form == "bltu":
            self.emit(f"bltu x0, x{c}, {top}")
        elif form == "bge":
            self.emit(f"bge x{c}, x0, {top}")
        else:
            rd = self.dst(exclude)
            self.emit(f"beq x{c}, x0, {done}")
            self.emit(f"jal x{rd}, {top}", rd)
        self.out.append(f"{done}:")

    def fwd_jal(self, kinds, depth, exclude=()):
        lab = self.label("j")
        rd = self.dst(exclude)
        self.emit(f"jal x{rd}, {lab}", rd)
        self.block(kinds, self.rng.randint(0, 3), depth + 1, exclude)
        self.out.append(f"{lab}:")

    def fwd_jalr(self, kinds, depth, exclude=()):
        t = self.dst(tuple(exclude) + (0,))
        if t == 0:
            return self.fwd_jal(kinds, depth, exclude)
        lab = self.label("r")
        off = self.rng.choice((0, 4, -4, 2047 - 2047 % 4, -2048, self.rng.randint(-512, 511) * 4))
        odd = self.rng.random() < 0.3            # jalr clears bit 0 of the sum
        adj = f"{lab}-({off})" + ("+1" if odd else "")
        self.emit(f"lui x{t}, %hi({adj})", t)
        # Keep the lui result >3 instructions away from its consumer, so the jalr itself
        # (not a lui->addi hazard) is what this item tests first.
        self.noread = (t,)
        for _ in range(3):
            self.alu(tuple(exclude) + (t,))
        self.noread = ()
        self.emit(f"addi x{t}, x{t}, %lo({adj})", t)
        for _ in range(self.rng.randint(0, 2)):
            self.alu(tuple(exclude) + (t,))
        rd = self.dst(exclude)
        self.emit(f"jalr x{rd}, {off}(x{t})", rd)
        self.block(kinds, self.rng.randint(0, 3), depth + 1, exclude)
        self.out.append(f"{lab}:")

    def call(self, kinds, depth, exclude=()):
        link = self.dst(tuple(exclude) + (0,))
        if link == 0:
            return self.fwd_jal(kinds, depth, exclude)
        lab = self.label("sub")
        self.emit(f"jal x{link}, {lab}", link)
        # Subroutine body: straight-line, never writes the link register.
        saved, self.out, saved_recent = self.out, [f"{lab}:"], self.recent
        for _ in range(self.rng.randint(0, 4)):
            self.simple(kinds, tuple(exclude) + (link,))
        rd = self.dst(tuple(exclude)) if self.rng.random() < 0.3 else 0
        self.emit(f"jalr x{rd}, 0(x{link})", rd)
        self.subs.append(self.out)
        self.out, self.recent = saved, saved_recent

    def block(self, kinds, n, depth, exclude=()):
        for _ in range(n):
            self.item(kinds, depth, exclude)

    def item(self, kinds, depth, exclude=()):
        p = self.profile
        r = self.rng.random()
        ctrl = depth < 3
        if p in ("branch", "full") and ctrl and r < 0.18:
            return self.fwd_branch(kinds, depth, exclude)
        if p in ("branch", "full") and ctrl and r < 0.26:
            return self.loop(kinds, depth, exclude)
        if p in ("jump", "full") and ctrl and 0.26 <= r < 0.38:
            return self.fwd_jal(kinds, depth, exclude)
        if p in ("jump", "full") and ctrl and 0.38 <= r < 0.48:
            return self.fwd_jalr(kinds, depth, exclude)
        if p in ("jump", "full") and ctrl and 0.48 <= r < 0.54 and depth == 0:
            return self.call(kinds, depth, exclude)
        if p in ("jump", "full") and ctrl and 0.54 <= r < 0.58:
            return self.loop(kinds, depth, exclude, jump=True)
        self.simple(kinds, exclude)

    # ---- whole program --------------------------------------------------------------
    def init_value(self):
        if self.rng.random() < 0.75:
            return self.rng.choice(INTERESTING)
        return self.rng.getrandbits(32)

    def program(self):
        rng = self.rng
        kinds = {"alu": ("alu",), "shift": ("shift", "shift", "alu"), "upper": ("upper", "alu"),
                 "branch": ("alu",), "jump": ("alu",), "mem": ("mem", "mem", "alu"),
                 "full": ("alu", "shift", "upper", "mem")}[self.profile]
        uses_mem = "mem" in kinds
        inits = [(r, self.init_value()) for r in self.pool]
        if uses_mem:
            inits += [(self.bases[0], "gdata+2048"), (self.bases[1], "gdata")]
        head = [f"# randgen profile={self.profile}", ".section .text.init", ".option norvc",
                ".global _start", "_start:"]
        # lui group, then addi group: each addi is >= 4 instructions after its lui.
        hi_lo = []
        for r, v in inits:
            if isinstance(v, str):
                hi_lo.append((r, f"%hi({v})", f"%lo({v})"))
            else:
                hi = ((v + 0x800) >> 12) & 0xFFFFF
                lo = ((v - (hi << 12)) + 0x800) % 0x1000 - 0x800
                hi_lo.append((r, f"{hi:#x}", str(lo)))
        pad = ["  addi x0, x0, 0"] * max(0, 4 - len(hi_lo))
        head += [f"  lui x{r}, {h}" for r, h, _ in hi_lo] + pad
        head += [f"  addi x{r}, x{r}, {lo}" for r, _, lo in hi_lo]
        n = rng.randint(*BODY_ITEMS)
        self.block(kinds, n, 0)
        tail = [".global halt", "halt:", "  j halt"]
        for s in self.subs:
            tail += s
        data = []
        if uses_mem:
            data = [".section .data", ".p2align 4", "gdata:"]
            data += [f"  .word {rng.getrandbits(32):#010x}" for _ in range(DATA_WORDS)]
        return "\n".join(head + ["# body"] + self.out + tail + data) + "\n"


def generate(seed, profile, index):
    return _Gen(seed, profile, index).program()
