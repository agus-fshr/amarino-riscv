from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, connect
from amaranth_boards.arty_a7 import ArtyA7_100Platform

from amarino.modules.regbank import RegBank
from amarino.modules.alu import ALU
from amarino.modules.decoder import Decoder
from amarino.modules.fetch import Fetch, ExternalImemFetch
from amarino.modules.opbuilder import opBuilder
from amarino.modules.immbuilder import Immbuilder
from amarino.modules.uart import UART32
from amarino.modules.addrbuilder_jmpctrl import Addrbuilder
from amarino.pipeline.decode_latch import Decode_latch
from amarino.pipeline.execute_latch import Execute_latch
from amarino.pipeline.retire_latch import Retire_latch
from amarino.pipeline.pipeline import Pipeline
from amarino.rvfi import RvfiMonitor, rvfi_signature
from amarino.bus_signatures import imem_bus
# from amarino.modules.clockWorks import Clockworks

class Top (Elaboratable):
    def __init__(self, imem="internal"):
        super().__init__()
        assert imem in ("internal", "external")
        self.imem = imem
        # Verification ports (see docs/rvfi.md). Unused by the FPGA build.
        self.rvfi = rvfi_signature().create(path=("rvfi",))
        self.ibus = imem_bus().create(path=("ibus",)) if imem == "external" else None

    def ports(self):
        """Top-level ports for Verilog export: RVFI, plus the instruction bus in external mode."""
        sigs = [value for _, _, value in rvfi_signature().flatten(self.rvfi)]
        if self.ibus is not None:
            sigs += [value for _, _, value in imem_bus().flatten(self.ibus)]
        return sigs

    def elaborate(self, platform):
        m = Module()

        m.submodules.regbank = regbank = RegBank()
        m.submodules.decode_latch = decode_latch = Decode_latch()
        m.submodules.execute_latch = execute_latch = Execute_latch()
        m.submodules.retire_latch = retire_latch = Retire_latch()
        m.submodules.alu = alu = ALU()
        m.submodules.decoder = decoder = Decoder()
        m.submodules.fetch = fetch = ExternalImemFetch() if self.imem == "external" else Fetch()
        m.submodules.opbuilder = opbuilder = opBuilder()
        m.submodules.imm_builder = imm_builder = Immbuilder()
        m.submodules.addr_builder = addr_builder = Addrbuilder()
        m.submodules.pipeline = pipeline = Pipeline()
        m.submodules.uart = uart = UART32()

        # m.submodules.clockworks = Clockworks = Clockworks(m, 21)
        
        self.decode_latch = decode_latch
        self.execute_latch = execute_latch
        self.retire_latch = retire_latch
        self.alu = alu
        self.regbank = regbank
        self.decoder = decoder
        self.fetch = fetch
        self.opbuilder = opbuilder
        self.imm_builder = imm_builder
        self.addr_builder = addr_builder
        self.pipeline = pipeline
        self.uart = uart
        
        # Fetch inputs
        connect(m, fetch.pc_update, addr_builder.PC_out)

        # Fetch outputs
        connect(m, fetch.pc, decode_latch.pc_in)
        connect(m, fetch.instr, decoder.instr)
        connect(m, fetch.instr, imm_builder.instr)

        # Imm builder outputs
        connect(m, imm_builder.imm_data, decode_latch.imm_data_in)
        
        # Decoder outputs
        connect(m, decoder.alu_flags, decode_latch.instr_flags_in)
        connect(m, decoder.alu_flags, imm_builder.instr_flags)
        connect(m, decoder.alu_flags, pipeline.instr_flags_fetch)
        connect(m, decoder.reg_addr, decode_latch.reg_address_in)
        connect(m, decoder.reg_addr, pipeline.reg_addr_fetch)
        connect(m, decoder.functions, decode_latch.alu_func_in)
        connect(m, decoder.branch_flags, decode_latch.branch_flags_in)
        
        # Decode latch outputs
        connect(m, decode_latch.instr_flags_out, execute_latch.instr_flags_in) 
        connect(m, decode_latch.instr_flags_out, addr_builder.instr_flags) 
        connect(m, decode_latch.instr_flags_out, pipeline.instr_flags_decode)
        connect(m, decode_latch.imm_data_out, execute_latch.imm_data_in)
        connect(m, decode_latch.imm_data_out, addr_builder.imm_data) 
        connect(m, decode_latch.alu_func_out, execute_latch.alu_func_in)
        # connect(m, decode_latch.reg_address_out.rs1_addr, regbank.reg_addr.rs1_addr)

        m.d.comb += [
            regbank.reg_addr.rs1_addr.eq(decode_latch.reg_address_out.rs1_addr),
            regbank.reg_addr.rs2_addr.eq(decode_latch.reg_address_out.rs2_addr),
            execute_latch.rd_in.eq(decode_latch.reg_address_out.rd_addr)
        ]
        # connect(m, decode_latch.reg_address_out.rs2_addr, regbank.reg_addr.rs2_addr)
        connect(m, decode_latch.reg_address_out, pipeline.reg_addr_decode)
        connect(m, decode_latch.pc_out, execute_latch.pc_in)
        connect(m, decode_latch.pc_out, addr_builder.PC_in)   
        connect(m, decode_latch.branch_flags_out, execute_latch.branch_flags_in)
        # connect(m, decode_latch.reg_address_out.rd_addr, execute_latch.rd_in)

        # regbank outputs
        connect(m, regbank.rs_buses, execute_latch.reg_data_in)
        connect(m, regbank.rs_buses, addr_builder.rs_data)
        connect(m, regbank.uart_reg, uart.data)
        
        # Execute latch outputs 
        connect(m, execute_latch.alu_func_out , alu.functions)
        connect(m, execute_latch.pc_out , opbuilder.pc)
        connect(m, execute_latch.imm_data_out , opbuilder.imm)
        connect(m, execute_latch.reg_data_out , opbuilder.data_regbank)
        connect(m, execute_latch.instr_flags_out , opbuilder.instr_flags)
        connect(m, execute_latch.instr_flags_out, alu.flags_in)
        connect(m, execute_latch.instr_flags_out, pipeline.instr_flags_execute)
        connect(m, execute_latch.branch_flags_out, pipeline.branch_flags_execute)
        connect(m, execute_latch.instr_flags_out, retire_latch.instr_flags_in)
        m.d.comb += [
            pipeline.rd_execute.eq(execute_latch.rd_out),
            retire_latch.rd_in.eq(execute_latch.rd_out)
            ]
        
        # Op Builder outputs
        connect(m, opbuilder.alu_buses, alu.data_buses)
        
        # ALU outputs
        connect(m, alu.rd_bus, retire_latch.rd_data_in)
        m.d.comb += [
            pipeline.alu_flag_n.eq(alu.n_flag),
            pipeline.alu_flag_z.eq(alu.z_flag)
        ]

        # Retire_Latch outputs
        connect(m, retire_latch.rd_data_out, regbank.rd_bus)
        m.d.comb += [
            regbank.reg_addr.rd_addr.eq(retire_latch.rd_out),
            pipeline.rd_retire.eq(retire_latch.rd_out)
            ]
        connect(m, retire_latch.instr_flags_out, regbank.instr_flags)
        connect(m, retire_latch.instr_flags_out, pipeline.instr_flags_retire)

        # Conexión de señales de control de latches
        m.d.comb += [
            decode_latch.decode_enable.eq(pipeline.decode_enable),
            decode_latch.decode_mux.eq(pipeline.decode_mux),
            execute_latch.enable.eq(pipeline.execute_enable),
            execute_latch.mux.eq(pipeline.execute_mux),
            retire_latch.enable.eq(pipeline.retire_enable),
            # retire_latch.mux.eq(pipeline.retire_mux),
            addr_builder.addrbuilder_enable.eq(pipeline.addr_builder_enable),
            addr_builder.mux.eq(pipeline.addr_builder_mux),
            fetch.resetn.eq(pipeline.fetch_resetn),
            fetch.enable.eq(pipeline.fetch_enable),
            regbank.we.eq(pipeline.regbank_we)
        ]
        connect(m, pipeline.uart_start, uart.start)

        # UART outputs
        connect(m, uart.ready, pipeline.uart_ready)

        # Instruction bus (external instruction memory mode only)
        if self.ibus is not None:
            connect(m, fetch.ibus, wiring.flipped(self.ibus))

        # RVFI trace port: observes only, drives nothing in the core
        m.submodules.rvfi = rvfi = RvfiMonitor()
        m.d.comb += [
            rvfi.fetch_addr.eq(fetch.pc.pc),
            rvfi.fetch_insn.eq(fetch.instr.instr),
            rvfi.fetch_valid.eq(~fetch.resetn & fetch.enable),
            rvfi.decode_enable.eq(decode_latch.decode_enable),
            rvfi.decode_mux.eq(decode_latch.decode_mux),
            rvfi.execute_enable.eq(execute_latch.enable),
            rvfi.execute_mux.eq(execute_latch.mux),
            rvfi.rs1_rdata.eq(execute_latch.reg_data_out.rs1_data),
            rvfi.rs2_rdata.eq(execute_latch.reg_data_out.rs2_data),
            rvfi.wb_we.eq(regbank.we),
            rvfi.wb_rd.eq(regbank.reg_addr.rd_addr),
            rvfi.wb_data.eq(regbank.rd_bus.rd_data),
        ]
        connect(m, rvfi.rvfi, wiring.flipped(self.rvfi))

        return m
    
if __name__ == "__main__":
    core = Top()
    platform = ArtyA7_100Platform()
    platform.build(core, do_build=True, do_program=False)