"""Locating the pinned tools installed by tools/setup_harness_tools.sh.

The harness only uses the pinned copies in .tools/ (or $HARNESS_TOOLS_DIR), never
whatever happens to be on PATH, so results don't depend on the machine.
"""

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = Path(os.environ.get("HARNESS_TOOLS_DIR", REPO / ".tools"))
CAD_BIN = TOOLS / "oss-cad-suite" / "bin"

_LOCATIONS = {
    "sail_riscv_sim": TOOLS / "sail" / "bin" / "sail_riscv_sim",
    "riscv-formal": TOOLS / "riscv-formal",
    "riscv-arch-test": TOOLS / "riscv-arch-test",
}


class ToolMissing(RuntimeError):
    pass


def tool(name):
    """Absolute path to a pinned tool, or ToolMissing with the setup command to run."""
    path = _LOCATIONS.get(name, CAD_BIN / name)
    if not path.exists():
        raise ToolMissing(f"{name} not found at {path}; run tools/setup_harness_tools.sh")
    return str(path)


def host_tool(name):
    """A tool the harness takes from the host (only clang/ld.lld; see harness/README.md)."""
    path = shutil.which(name)
    if path is None:
        raise ToolMissing(f"{name} not found on PATH")
    return path


def configure_amaranth():
    """Make Amaranth's Verilog backend use the pinned yosys."""
    # Set even if missing, so Amaranth fails loudly instead of using another yosys.
    os.environ["YOSYS"] = str(CAD_BIN / "yosys")
    os.environ["AMARANTH_USE_YOSYS"] = "system"


def cad_env():
    """Environment for running oss-cad-suite tools (they expect their bin dir on PATH)."""
    env = dict(os.environ)
    env["PATH"] = f"{CAD_BIN}{os.pathsep}{env.get('PATH', '')}"
    return env
