/* Gate 4 benchmark start-up: set up the stack, call main, keep its return value in
   a0 and stop in a self-loop at _halt. The harness runs the reference simulator
   until the PC reaches _halt and reads the result from a0 there. The value is also
   stored to `bench_result` for anyone inspecting memory. .bss needs no clearing:
   both the core image and the reference simulator start from zeroed memory. */
#undef main   /* a benchmark may be built with -Dmain=... to wrap its own main */
    .section .text.init, "ax"
    .globl _start
_start:
    la    sp, _stack_top
    call  main
    la    t0, bench_result
    sw    a0, 0(t0)
    .globl _halt
_halt:
    j     _halt

    .section .bss
    .balign 4
    .globl bench_result
bench_result:
    .word 0
