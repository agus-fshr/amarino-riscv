/* Minimal freestanding runtime for the gate 4 benchmarks (RV32I: no M extension).

   Provides the string functions the benchmarks call, a printf that discards its
   output (the core has no console the harness reads), and the integer libcalls
   clang emits for *, / and % on rv32i. All plain C, written so the compiler cannot
   turn a helper into a call to itself. */

#include <stddef.h>
#include <stdarg.h>

void *memcpy(void *__restrict dst, const void *__restrict src, size_t n)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    while (n--) {
        *d++ = *s++;
        __asm__ volatile ("" ::: "memory");   /* keep loop-idiom from forming memcpy */
    }
    return dst;
}

void *memmove(void *dst, const void *src, size_t n)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    if (d < s) {
        while (n--) {
            *d++ = *s++;
            __asm__ volatile ("" ::: "memory");
        }
    } else {
        while (n--) {
            d[n] = s[n];
            __asm__ volatile ("" ::: "memory");
        }
    }
    return dst;
}

void *memset(void *dst, int c, size_t n)
{
    unsigned char *d = dst;
    while (n--) {
        *d++ = (unsigned char)c;
        __asm__ volatile ("" ::: "memory");
    }
    return dst;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = a, *y = b;
    for (; n; n--, x++, y++)
        if (*x != *y)
            return *x - *y;
    return 0;
}

size_t strlen(const char *s)
{
    const char *p = s;
    while (*p)
        p++;
    return p - s;
}

char *strcpy(char *dst, const char *src)
{
    char *d = dst;
    while ((*d++ = *src++))
        __asm__ volatile ("" ::: "memory");
    return dst;
}

int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b)
        a++, b++;
    return (unsigned char)*a - (unsigned char)*b;
}

/* Output is discarded. */
int printf(const char *fmt, ...) { (void)fmt; return 0; }
int puts(const char *s) { (void)s; return 0; }
int putchar(int c) { return c; }

/* ---- RV32I integer libcalls (libgcc/compiler-rt names) --------------------- */

unsigned int __mulsi3(unsigned int a, unsigned int b)
{
    unsigned int r = 0;
    while (b) {
        if (b & 1)
            r += a;
        a <<= 1;
        b >>= 1;
    }
    return r;
}

static unsigned int udivmod(unsigned int n, unsigned int d, unsigned int *rem)
{
    unsigned int q = 0, r = 0;
    int i;
    if (d == 0) {                 /* RISC-V M semantics: q = all ones, r = n */
        *rem = n;
        return 0xFFFFFFFFu;
    }
    for (i = 31; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1);
        if (r >= d) {
            r -= d;
            q |= 1u << i;
        }
    }
    *rem = r;
    return q;
}

unsigned int __udivsi3(unsigned int n, unsigned int d)
{
    unsigned int r;
    return udivmod(n, d, &r);
}

unsigned int __umodsi3(unsigned int n, unsigned int d)
{
    unsigned int r;
    udivmod(n, d, &r);
    return r;
}

int __divsi3(int n, int d)
{
    unsigned int r;
    unsigned int un = n < 0 ? -(unsigned int)n : (unsigned int)n;
    unsigned int ud = d < 0 ? -(unsigned int)d : (unsigned int)d;
    unsigned int q = udivmod(un, ud, &r);
    if (d == 0)
        return -1;
    return ((n < 0) != (d < 0)) ? -(int)q : (int)q;
}

int __modsi3(int n, int d)
{
    unsigned int r;
    unsigned int un = n < 0 ? -(unsigned int)n : (unsigned int)n;
    unsigned int ud = d < 0 ? -(unsigned int)d : (unsigned int)d;
    udivmod(un, ud, &r);
    if (d == 0)
        return n;
    return n < 0 ? -(int)r : (int)r;
}
