/* Freestanding stand-in for the gate 4 benchmarks: printf discards its output. */
#ifndef GATE4_STDIO_H
#define GATE4_STDIO_H
int printf(const char *fmt, ...);
int puts(const char *s);
int putchar(int c);
#endif
