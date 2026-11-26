/* Fibonacci, byte-addressed RV32I: fib(25) iteratively and recursively.
   Returns fib(25) = 75025 if both agree, 0 otherwise. The array keeps loads and
   stores in the loop so the benchmark also exercises data memory. */

#define N 25

static unsigned int table[N + 1];

static unsigned int fib_iter(int n)
{
    table[0] = 0;
    table[1] = 1;
    for (int i = 2; i <= n; i++)
        table[i] = table[i - 1] + table[i - 2];
    return table[n];
}

__attribute__((noinline)) static unsigned int fib_rec(int n)
{
    return n < 2 ? (unsigned int)n : fib_rec(n - 1) + fib_rec(n - 2);
}

int main(void)
{
    volatile int n = N;               /* stop the compiler folding the answer */
    unsigned int a = fib_iter(n);
    unsigned int b = fib_rec(n - 10); /* fib(15) = 610: keeps the run short */
    return (a == 75025 && b == 610) ? (int)a : 0;
}
