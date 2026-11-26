/* CoreMark port for the gate 4 harness (bare RV32I, no timer, no console).
   Based on eembc/coremark barebones/core_portme.c (Apache-2.0).

   The core has no timer. CoreMark rejects runs shorter than 10 s, so the "clock"
   here reports exactly 10 s between start_time() and stop_time(). That only keeps
   CoreMark's self-check honest about everything else (the list/matrix/state CRCs);
   no CoreMark score is reported. The harness measures the run itself (retired
   instructions and cycles).

   core_main.c is compiled with -Dmain=coremark_main. There is no console, so
   ee_printf prints nothing; instead it watches for CoreMark's own verdict lines, and
   main() below returns 0xC0DE only if CoreMark recognised the 2K performance-run seeds,
   reported "Correct operation validated" and printed no error line. */
#include "coremark.h"

static int seen_2k_perf, seen_validated, seen_error;

static int starts_with(const char *s, const char *prefix)
{
    while (*prefix)
        if (*s++ != *prefix++)
            return 0;
    return 1;
}

static int contains(const char *s, const char *word)
{
    for (; *s; s++)
        if (starts_with(s, word))
            return 1;
    return 0;
}

int ee_printf(const char *fmt, ...)
{
    if (starts_with(fmt, "2K performance run parameters"))
        seen_2k_perf = 1;
    if (starts_with(fmt, "Correct operation validated"))
        seen_validated = 1;
    if (contains(fmt, "ERROR") || contains(fmt, "Errors detected") || contains(fmt, "Cannot validate"))
        seen_error = 1;
    return 0;
}

#undef main
extern int coremark_main(void);

int main(void)
{
    coremark_main();
    return (seen_2k_perf && seen_validated && !seen_error) ? 0xC0DE : 0xBAD;
}

#if VALIDATION_RUN
volatile ee_s32 seed1_volatile = 0x3415;
volatile ee_s32 seed2_volatile = 0x3415;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PERFORMANCE_RUN
volatile ee_s32 seed1_volatile = 0x0;
volatile ee_s32 seed2_volatile = 0x0;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PROFILE_RUN
volatile ee_s32 seed1_volatile = 0x8;
volatile ee_s32 seed2_volatile = 0x8;
volatile ee_s32 seed3_volatile = 0x8;
#endif
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;

#define EE_TICKS_PER_SEC 1000

static CORETIMETYPE start_time_val, stop_time_val;

void start_time(void) { start_time_val = 0; }
void stop_time(void) { stop_time_val = 10 * EE_TICKS_PER_SEC; }
CORE_TICKS get_time(void) { return (CORE_TICKS)(stop_time_val - start_time_val); }
secs_ret time_in_secs(CORE_TICKS ticks) { return (secs_ret)ticks / EE_TICKS_PER_SEC; }

ee_u32 default_num_contexts = 1;

void portable_init(core_portable *p, int *argc, char *argv[])
{
    (void)argc;
    (void)argv;
    p->portable_id = 1;
}

void portable_fini(core_portable *p) { p->portable_id = 0; }
