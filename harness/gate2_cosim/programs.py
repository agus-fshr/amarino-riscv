"""Building gate 2's input programs: riscv-arch-test tests and random programs.

Every program is linked with harness/common/link.ld, starts at the reset PC and ends
in the self-loop at the global symbol `halt`.
"""

from pathlib import Path

from harness.common.program import RESET_PC, BuildError, build, load_elf
from harness.common.tools import tool
from harness.gate2_cosim import randgen

HERE = Path(__file__).resolve().parent
ARCH_ENV = HERE / "archtest_env"


def arch_root():
    return Path(tool("riscv-arch-test")) / "riscv-test-suite"


def arch_tests():
    """Names of the rv32i_m/I tests, e.g. 'add-01'."""
    src = arch_root() / "rv32i_m" / "I" / "src"
    names = sorted(p.stem for p in src.glob("*.S"))
    if not names:
        raise BuildError(f"no tests in {src}")
    return names


# Flags for building an unmodified arch test for this target:
#   XLEN=32, TEST_CASE_1   what riscof would pass for an RV32I target and these tests
#   UNROLLSZ=2             arch_test.h's alignment knob (default 5, i.e. 32 bytes). With
#                          32-byte alignment the linker would place the test's .text.init
#                          at image_base+0x20 and leave zero (illegal) words between the
#                          reset PC and the test; 4-byte alignment makes the test start
#                          exactly at the reset PC. It only changes alignment padding.
#   _start=rvtest_entry_point  the entry symbol link.ld expects
ARCH_FLAGS = ["-DXLEN=32", "-DTEST_CASE_1=True", "-DUNROLLSZ=2", f"-I{ARCH_ENV}",
              "-Wl,--defsym=_start=rvtest_entry_point"]


def _checked(elf):
    img = load_elf(elf)
    if img.entry != RESET_PC:
        raise BuildError(f"{elf}: entry {img.entry:#x} is not the reset PC {RESET_PC:#x}")
    if "halt" not in img.symbols:
        raise BuildError(f"{elf}: no `halt` symbol")
    return img


def build_arch(name, outdir):
    src = arch_root() / "rv32i_m" / "I" / "src" / f"{name}.S"
    elf = build([src], Path(outdir) / f"{name}.elf",
                extra_flags=ARCH_FLAGS + [f"-I{arch_root() / 'env'}"])
    return elf, _checked(elf)


def build_rand(seed, profile, index, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    asm = outdir / "prog.S"
    asm.write_text(randgen.generate(seed, profile, index))
    elf = build([asm], outdir / "prog.elf")
    return elf, _checked(elf)


def build_asm(text, outdir, name="prog"):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    asm = outdir / f"{name}.S"
    asm.write_text(text)
    elf = build([asm], outdir / f"{name}.elf")
    return elf, _checked(elf)
