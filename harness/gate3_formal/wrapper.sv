// riscv-formal wrapper for the amarino core (Top, imem="external").
//
// The only environment the core has is its instruction bus. ibus__rdata is left
// completely unconstrained (`rvformal_rand_reg` is `rand reg`, i.e. $anyseq, under
// yosys): every cycle the solver may return any 32-bit word, so the checks cover
// every possible instruction stream, including words that are not valid RV32I.
// There is no data bus and no interrupt or stall input, so nothing else needs
// modelling. This file adds NO assumptions: reset is driven by rvfi_testbench
// (asserted in the initial cycle only, `assume(reset == $initstate)`).
module rvfi_wrapper (
	input clock,
	input reset,
	`RVFI_OUTPUTS
);
	(* keep *) `rvformal_rand_reg [31:0] ibus_rdata;
	(* keep *) wire [31:0] ibus_addr;
	(* keep *) wire uart_tx;

	top uut (
		.clk            (clock),
		.rst            (reset),
		.ibus__addr     (ibus_addr),
		.ibus__rdata    (ibus_rdata),
		.uart0_tx_o     (uart_tx),

		.rvfi__valid    (rvfi_valid),
		.rvfi__order    (rvfi_order),
		.rvfi__insn     (rvfi_insn),
		.rvfi__trap     (rvfi_trap),
		.rvfi__halt     (rvfi_halt),
		.rvfi__intr     (rvfi_intr),
		.rvfi__mode     (rvfi_mode),
		.rvfi__ixl      (rvfi_ixl),
		.rvfi__rs1_addr (rvfi_rs1_addr),
		.rvfi__rs2_addr (rvfi_rs2_addr),
		.rvfi__rs1_rdata(rvfi_rs1_rdata),
		.rvfi__rs2_rdata(rvfi_rs2_rdata),
		.rvfi__rd_addr  (rvfi_rd_addr),
		.rvfi__rd_wdata (rvfi_rd_wdata),
		.rvfi__pc_rdata (rvfi_pc_rdata),
		.rvfi__pc_wdata (rvfi_pc_wdata),
		.rvfi__mem_addr (rvfi_mem_addr),
		.rvfi__mem_rmask(rvfi_mem_rmask),
		.rvfi__mem_wmask(rvfi_mem_wmask),
		.rvfi__mem_rdata(rvfi_mem_rdata),
		.rvfi__mem_wdata(rvfi_mem_wdata)
	);
endmodule
