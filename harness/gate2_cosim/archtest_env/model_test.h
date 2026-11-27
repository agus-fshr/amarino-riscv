// riscv-arch-test target environment for the harness (gate 2).
//
// The tests are built unmodified with clang against harness/common/link.ld and run
// on Sail and on the core; gate 2 compares their full retirement traces, which is
// stronger than comparing signatures. So this target only has to:
//   - not need anything beyond RV32I (no HTIF, no CSRs, no traps), and
//   - end every test in a self-loop at the global symbol `halt`, which the harness
//     uses as the stop PC on Sail and as the end of the reference trace.
// The signature region is kept (RVMODEL_DATA_BEGIN/END just label it) because the
// tests store into it; those stores are part of the compared trace.
#ifndef _HARNESS_MODEL_TEST_H
#define _HARNESS_MODEL_TEST_H

#define RVMODEL_BOOT

// Self-loop at a known symbol. Nothing after it executes.
#define RVMODEL_HALT \
  .global halt;      \
  halt:              \
    j halt;

#define RVMODEL_DATA_BEGIN \
  .align 4;                \
  .global begin_signature; \
  begin_signature:

#define RVMODEL_DATA_END \
  .align 4;              \
  .global end_signature; \
  end_signature:

// No I/O on this platform.
#define RVMODEL_IO_INIT
#define RVMODEL_IO_WRITE_STR(_R, _STR)
#define RVMODEL_IO_CHECK()
#define RVMODEL_IO_ASSERT_GPR_EQ(_S, _R, _I)
#define RVMODEL_IO_ASSERT_SFPR_EQ(_F, _R, _I)
#define RVMODEL_IO_ASSERT_DFPR_EQ(_D, _R, _I)

// No interrupts on this platform; the I tests never raise them. Empty macros
// (rather than arch_test.h's default handler) because there is nothing to clear.
#define RVMODEL_SET_MSW_INT
#define RVMODEL_CLR_MSW_INT
#define RVMODEL_CLR_MTIMER_INT
#define RVMODEL_CLR_MEXT_INT
#define RVMODEL_SET_SSW_INT
#define RVMODEL_CLR_SSW_INT
#define RVMODEL_CLR_STIMER_INT
#define RVMODEL_CLR_SEXT_INT
#define RVMODEL_SET_VSW_INT
#define RVMODEL_CLR_VSW_INT
#define RVMODEL_CLR_VTIMER_INT
#define RVMODEL_CLR_VEXT_INT

#endif
