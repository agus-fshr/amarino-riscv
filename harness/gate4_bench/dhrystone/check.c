/* Gate 4 Dhrystone wrapper. riscv-tests' dhrystone_main.c is compiled with
   -Dmain=dhrystone_main; this main runs it and checks the final global values the
   benchmark itself lists as "should be". (The records behind Ptr_Glob live in
   dhrystone_main's alloca'd frame and are gone once it returns, so they are not
   checked.) Returns 0xD4 if everything matches, else the number of the first
   failing check. */
#undef main
#include "dhrystone.h"

long gate4_fake_cycles;

extern int dhrystone_main(int argc, char **argv);
extern int Int_Glob, Arr_1_Glob[50], Arr_2_Glob[50][50];
extern Boolean Bool_Glob;
extern char Ch_1_Glob, Ch_2_Glob;

int main(void)
{
    if (dhrystone_main(0, 0) != 0) return 1;
    if (Int_Glob != 5) return 2;
    if (Bool_Glob != 1) return 3;
    if (Ch_1_Glob != 'A') return 4;
    if (Ch_2_Glob != 'B') return 5;
    if (Arr_1_Glob[8] != 7) return 6;
    if (Arr_2_Glob[8][7] != NUMBER_OF_RUNS + 10) return 7;
    return 0xD4;
}
