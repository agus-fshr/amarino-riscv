// Verilator testbench for Top(imem="external"): serves the instruction bus from an
// image and prints one line per RVFI record.
//
// usage: coresim IMAGE.hex MAX_RETIRE MAX_CYCLES
// IMAGE.hex: one 32-bit word per line (hex); word i is at core word address i.
// Words outside the image read as 0, like a zero-initialised memory.
// Output: "R order insn pc_rdata pc_wdata rs1_addr rs1_rdata rs2_addr rs2_rdata
//          rd_addr rd_wdata trap mem_addr mem_rmask mem_wmask mem_rdata mem_wdata cycle"
// then "END retired cycles status" where status is "limit" or "timeout".
#include <cstdio>
#include <cstdlib>
#include <cinttypes>
#include <vector>
#include "Vtop.h"
#include "verilated.h"

// Verilator escapes "__" in port names (Amaranth names them rvfi__valid, ibus__addr, ...).
#define RVFI(x) rvfi___05F##x
#define IBUS(x) ibus___05F##x

int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage: %s IMAGE MAX_RETIRE MAX_CYCLES\n", argv[0]); return 2; }
    std::vector<uint32_t> mem;
    FILE* f = fopen(argv[1], "r");
    if (!f) { perror(argv[1]); return 2; }
    unsigned w;
    while (fscanf(f, "%x", &w) == 1) mem.push_back(w);
    fclose(f);
    const uint64_t max_retire = strtoull(argv[2], nullptr, 0);
    const uint64_t max_cycles = strtoull(argv[3], nullptr, 0);

    VerilatedContext ctx;
    Vtop top(&ctx);
    auto rd = [&](uint32_t a) -> uint32_t { return a < mem.size() ? mem[a] : 0; };

    // One reset cycle.
    top.clk = 0; top.rst = 1; top.IBUS(rdata) = 0; top.eval();
    top.clk = 1; top.eval();
    top.clk = 0; top.rst = 0; top.eval();

    uint64_t retired = 0, cycle = 0;
    for (; cycle < max_cycles && retired < max_retire; cycle++) {
        top.clk = 0; top.eval();
        if (top.RVFI(valid)) {
            printf("R %" PRIu64 " %08x %08x %08x %u %08x %u %08x %u %08x %u %08x %x %x %08x %08x %" PRIu64 "\n",
                   (uint64_t)top.RVFI(order), top.RVFI(insn), top.RVFI(pc_rdata), top.RVFI(pc_wdata),
                   top.RVFI(rs1_addr), top.RVFI(rs1_rdata), top.RVFI(rs2_addr), top.RVFI(rs2_rdata),
                   top.RVFI(rd_addr), top.RVFI(rd_wdata), top.RVFI(trap),
                   top.RVFI(mem_addr), top.RVFI(mem_rmask), top.RVFI(mem_wmask),
                   top.RVFI(mem_rdata), top.RVFI(mem_wdata), cycle);
            retired++;
        }
        const uint32_t addr = top.IBUS(addr);
        top.clk = 1; top.eval();          // rising edge
        top.IBUS(rdata) = rd(addr);       // synchronous read: data for the address of this cycle
    }
    printf("END %" PRIu64 " %" PRIu64 " %s\n", retired, cycle, retired >= max_retire ? "limit" : "timeout");
    top.final();
    return 0;
}
