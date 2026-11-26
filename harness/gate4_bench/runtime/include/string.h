/* Freestanding stand-in for the gate 4 benchmarks (implemented in runtime/libc.c). */
#ifndef GATE4_STRING_H
#define GATE4_STRING_H
#include <stddef.h>
void *memcpy(void *__restrict dst, const void *__restrict src, size_t n);
void *memmove(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);
int memcmp(const void *a, const void *b, size_t n);
size_t strlen(const char *s);
char *strcpy(char *dst, const char *src);
int strcmp(const char *a, const char *b);
#endif
