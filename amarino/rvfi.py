"""RISC-V Formal Interface (RVFI) trace port for the Amarino core.

The monitor observes the pipeline; it never drives anything back into it.
Per-field rules, and what each shadow register can and cannot hide, are in
docs/rvfi.md. Keep the two in sync.
"""

from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


def rvfi_signature():
    """RVFI with NRET=1, XLEN=32, ILEN=32 (riscv-formal docs/rvfi.md)."""
    return wiring.Signature({
        "valid": Out(1),
        "order": Out(64),
        "insn": Out(32),
        "trap": Out(1),
        "halt": Out(1),
        "intr": Out(1),
        "mode": Out(2),
        "ixl": Out(2),
        "rs1_addr": Out(5),
        "rs2_addr": Out(5),
        "rs1_rdata": Out(32),
        "rs2_rdata": Out(32),
        "rd_addr": Out(5),
        "rd_wdata": Out(32),
        "pc_rdata": Out(32),
        "pc_wdata": Out(32),
        "mem_addr": Out(32),
        "mem_rmask": Out(4),
        "mem_wmask": Out(4),
        "mem_rdata": Out(32),
        "mem_wdata": Out(32),
    })


# Opcodes whose encoding architecturally reads rs1 / rs2.
_READS_RS1 = (0b0110011, 0b0010011, 0b0000011, 0b0100011, 0b1100011, 0b1100111)
_READS_RS2 = (0b0110011, 0b0100011, 0b1100011)


class RvfiMonitor(wiring.Component):
    # IF: what fetch hands to decode this cycle, and the address it was read from
    # one cycle earlier (the instruction memory read is synchronous).
    fetch_addr: In(32)      # word address driven to instruction memory this cycle
    fetch_insn: In(32)      # instruction presented to the decoder this cycle
    fetch_valid: In(1)      # fetch is passing fetch_insn on (enable & ~resetn)

    # Pipeline latch controls, exactly as the latches see them.
    decode_enable: In(1)
    decode_mux: In(1)
    execute_enable: In(1)
    execute_mux: In(1)

    # EX/WB taps.
    rs1_rdata: In(32)       # execute latch operand registers
    rs2_rdata: In(32)
    wb_we: In(1)            # register file write port, as the register file sees it
    wb_rd: In(5)
    wb_data: In(32)

    rvfi: Out(rvfi_signature())

    def elaborate(self, platform):
        m = Module()

        # ---- IF: address of the instruction now leaving fetch -------------------
        if_addr = Signal(32)       # word address issued one cycle ago
        if_issued = Signal()       # a read has been issued since reset
        m.d.sync += [if_addr.eq(self.fetch_addr), if_issued.eq(1)]
        if_valid = self.fetch_valid & if_issued

        # ---- ID / EX shadows: same enable/mux as the real latches ---------------
        id_valid, id_insn, id_pc = Signal(), Signal(32), Signal(32)
        ex_valid, ex_insn, ex_pc = Signal(), Signal(32), Signal(32)

        with m.If(self.decode_mux):
            m.d.sync += id_valid.eq(0)
        with m.Elif(self.decode_enable):
            m.d.sync += [id_valid.eq(if_valid), id_insn.eq(self.fetch_insn), id_pc.eq(if_addr)]

        with m.If(self.execute_mux):
            m.d.sync += ex_valid.eq(0)
        with m.Elif(self.execute_enable):
            m.d.sync += [ex_valid.eq(id_valid), ex_insn.eq(id_insn), ex_pc.eq(id_pc)]

        # ---- What the instruction in EX does this cycle -------------------------
        opcode = ex_insn[0:7]
        reads_rs1 = opcode.matches(*_READS_RS1)
        reads_rs2 = opcode.matches(*_READS_RS2)
        cur_rs1_addr = Mux(reads_rs1, ex_insn[15:20], 0)
        cur_rs2_addr = Mux(reads_rs2, ex_insn[20:25], 0)
        writes = self.wb_we & (self.wb_rd != 0)   # same condition the register file uses

        # ---- One-retirement holding stage ---------------------------------------
        # pc_wdata of instruction N is the pc of the next instruction that actually
        # retires, so a record is published when its successor reaches EX.
        h = {name: Signal(width, name=f"held_{name}") for name, width in [
            ("valid", 1), ("insn", 32), ("pc", 32), ("rs1_addr", 5), ("rs2_addr", 5),
            ("rs1_rdata", 32), ("rs2_rdata", 32), ("rd_addr", 5), ("rd_wdata", 32)]}
        order = Signal(64)

        publish = ex_valid & h["valid"]
        with m.If(ex_valid):
            m.d.sync += [
                h["valid"].eq(1),
                h["insn"].eq(ex_insn),
                h["pc"].eq(ex_pc),
                h["rs1_addr"].eq(cur_rs1_addr),
                h["rs2_addr"].eq(cur_rs2_addr),
                h["rs1_rdata"].eq(Mux(cur_rs1_addr != 0, self.rs1_rdata, 0)),
                h["rs2_rdata"].eq(Mux(cur_rs2_addr != 0, self.rs2_rdata, 0)),
                h["rd_addr"].eq(Mux(writes, self.wb_rd, 0)),
                h["rd_wdata"].eq(Mux(writes, self.wb_data, 0)),
            ]
        with m.If(publish):
            m.d.sync += order.eq(order + 1)

        r = self.rvfi
        m.d.comb += [
            r.valid.eq(publish),
            r.order.eq(order),
            r.insn.eq(h["insn"]),
            r.trap.eq(0),
            r.halt.eq(0),
            r.intr.eq(0),
            r.mode.eq(3),
            r.ixl.eq(1),
            r.rs1_addr.eq(h["rs1_addr"]),
            r.rs2_addr.eq(h["rs2_addr"]),
            r.rs1_rdata.eq(h["rs1_rdata"]),
            r.rs2_rdata.eq(h["rs2_rdata"]),
            r.rd_addr.eq(h["rd_addr"]),
            r.rd_wdata.eq(h["rd_wdata"]),
            # The core counts PCs in words; RVFI PCs are byte addresses.
            r.pc_rdata.eq(Cat(C(0, 2), h["pc"][0:30])),
            r.pc_wdata.eq(Cat(C(0, 2), ex_pc[0:30])),
            # No data memory port exists, so mem_* stay zero.
            r.mem_addr.eq(0),
            r.mem_rmask.eq(0),
            r.mem_wmask.eq(0),
            r.mem_rdata.eq(0),
            r.mem_wdata.eq(0),
        ]
        return m
