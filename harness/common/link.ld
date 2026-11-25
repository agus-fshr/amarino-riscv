/* Linker script for every program the harness runs (see spec/platform.toml).
   Word 0 is a pad because the platform's reset PC is image_base + 4. */
OUTPUT_ARCH(riscv)
ENTRY(_start)
SECTIONS
{
  . = 0x80000000;
  .reset_pad : { LONG(0x00000013) }
  .text : { KEEP(*(.text.init)) *(.text .text.*) }
  . = ALIGN(16);
  .tohost : { *(.tohost) }
  .rodata : { *(.rodata .rodata.* .srodata .srodata.*) }
  .data : { *(.data .data.* .sdata .sdata.*) }
  .bss : { *(.bss .bss.* .sbss .sbss.* COMMON) }
  . = ALIGN(16);
  _end = .;
  . += 0x4000;
  _stack_top = .;
}
