/* Replaces riscv-tests' benchmarks/common/util.h for Dhrystone on this platform.

   The core has no CSRs, so mcycle cannot be read. Dhrystone only uses the timer to
   decide whether the run was long enough (User_Time >= Too_Small_Time) and to print
   a score, which nothing reads. Each read returns a value 1000 higher than the last,
   so one pass of NUMBER_OF_RUNS is accepted. The harness measures the run itself
   (retired instructions and cycles), not Dhrystone's own timer. */
#ifndef GATE4_DHRY_UTIL_H
#define GATE4_DHRY_UTIL_H
extern long gate4_fake_cycles;
#define read_csr(reg) (gate4_fake_cycles += 1000)
static inline void setStats(int enable) { (void)enable; }
#endif
