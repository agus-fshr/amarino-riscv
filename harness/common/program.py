"""Building RISC-V programs and loading them as core memory images.

Programs are built with the host's clang/lld (the one tool not taken from .tools/,
see harness/README.md) for plain RV32I and linked with common/link.ld.
"""

import struct
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from harness.common.tools import host_tool

HERE = Path(__file__).resolve().parent
SPEC = HERE.parent / "spec"
LINK_LD = HERE / "link.ld"

with open(SPEC / "platform.toml", "rb") as _f:
    PLATFORM = tomllib.load(_f)
IMAGE_BASE = PLATFORM["image_base"]
RESET_PC = PLATFORM["reset_pc"]

CFLAGS = ["--target=riscv32-unknown-elf", "-march=rv32i", "-mabi=ilp32", "-mno-relax",
          "-nostdlib", "-nostartfiles", "-static", "-ffreestanding", "-fno-pic",
          "-fuse-ld=lld", f"-Wl,-T,{LINK_LD}", "-Wl,--no-relax"]


class BuildError(RuntimeError):
    pass


def build(sources, out_elf, extra_flags=(), link_ld=None):
    """Compile/assemble `sources` into an ELF. Raises BuildError with clang's output."""
    out_elf = Path(out_elf)
    out_elf.parent.mkdir(parents=True, exist_ok=True)
    flags = list(CFLAGS)
    if link_ld is not None:
        flags = [f for f in flags if not f.startswith("-Wl,-T,")] + [f"-Wl,-T,{link_ld}"]
    cmd = [host_tool("clang"), *flags, *extra_flags, "-o", str(out_elf), *map(str, sources)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise BuildError(f"{' '.join(cmd)}\n{r.stderr}")
    return out_elf


@dataclass
class Image:
    words: list          # core word address N -> 32-bit word at IMAGE_BASE + 4N
    entry: int
    symbols: dict


def load_elf(path):
    """Little-endian ELF32 loader: PT_LOAD segments into a word image based at IMAGE_BASE."""
    data = Path(path).read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise ValueError(f"{path}: not a little-endian ELF32 file")
    (e_type, e_machine, _, e_entry, e_phoff, e_shoff, _, _, e_phentsize, e_phnum,
     e_shentsize, e_shnum, e_shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    if e_machine != 243:
        raise ValueError(f"{path}: not a RISC-V ELF")
    mem = bytearray()
    for i in range(e_phnum):
        p_type, p_offset, _, p_paddr, p_filesz, p_memsz, _, _ = struct.unpack_from(
            "<IIIIIIII", data, e_phoff + i * e_phentsize)
        if p_type != 1 or p_memsz == 0:
            continue
        if p_paddr < IMAGE_BASE:
            raise ValueError(f"{path}: segment at {p_paddr:#x} below image base {IMAGE_BASE:#x}")
        off = p_paddr - IMAGE_BASE
        end = off + p_memsz
        if len(mem) < end:
            mem.extend(b"\0" * (end - len(mem)))
        mem[off:off + p_filesz] = data[p_offset:p_offset + p_filesz]
    mem.extend(b"\0" * (-len(mem) % 4))
    words = list(struct.unpack(f"<{len(mem) // 4}I", mem))
    return Image(words, e_entry, _symbols(data, e_shoff, e_shentsize, e_shnum))


def _symbols(data, shoff, shentsize, shnum):
    secs = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    out = {}
    for s in secs:
        if s[1] != 2:   # SHT_SYMTAB
            continue
        strtab = secs[s[6]]
        for j in range(s[5] // 16):
            name_off, value, _, _, _, _ = struct.unpack_from("<IIIBBH", data, s[4] + j * 16)
            if name_off:
                end = data.index(b"\0", strtab[4] + name_off)
                out[data[strtab[4] + name_off:end].decode()] = value
    return out
