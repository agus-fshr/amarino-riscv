"""Loading the core under test and turning it into netlists.

The core is imported from `amarino` on sys.path. `python -m harness --core-dir DIR` puts
DIR first, which is how mutants are tested.
"""

import gc
import importlib
import traceback
import warnings
from pathlib import Path
from types import SimpleNamespace

from amaranth import Signal
from amaranth.hdl import Fragment
from amaranth.back import rtlil, verilog
from amaranth.lib import wiring


class StubPlatform:
    """Just enough of a platform for elaboration outside a board build.

    UART32 requests its `uart` pin unconditionally; the harness hands it a plain
    signal instead. Anything else requested is reported, never silently faked.
    """
    default_clk_frequency = 100e6

    def __init__(self):
        self.pins = {}

    def request(self, name, number=0, **kwargs):
        if name != "uart":
            raise LookupError(f"harness StubPlatform has no resource {name!r}#{number}")
        sig = Signal(name=f"{name}{number}_tx_o")
        self.pins[(name, number)] = sig
        return SimpleNamespace(tx=SimpleNamespace(o=sig))


def top_class():
    return importlib.import_module("amarino.top").Top


def new_top(imem="external"):
    return top_class()(imem=imem)


def elaborate(elaboratable):
    """Elaborate with the stub platform; returns (fragment, warnings, platform)."""
    platform = StubPlatform()
    caught = []

    def record(message, category, filename, lineno, file=None, line=None):
        # Attribute the warning to the innermost frame inside the core, not to the
        # Amaranth internals that raised it.
        for fr in reversed(traceback.extract_stack()[:-1]):
            rel = core_relative(fr.filename)
            if rel is not None:
                filename, lineno = rel, fr.lineno
                break
        caught.append(warnings.WarningMessage(message, category, filename, lineno))

    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.showwarning = record
        frag = Fragment.get(elaboratable, platform)
        gc.collect()  # UnusedElaboratable is reported from __del__
    return frag, caught, platform


def core_relative(path):
    """'amarino/...' for a file of the core under test, else None."""
    parts = Path(path).parts
    if "amarino" in parts:
        i = len(parts) - 1 - parts[::-1].index("amarino")
        return str(Path(*parts[i:]))
    return None


def ports_of(elaboratable, platform):
    """Top-level ports: the declared verification ports (Top.ports()) or a component's
    signature, plus any pins the stub platform handed out."""
    if hasattr(elaboratable, "ports") and callable(elaboratable.ports):
        ports = list(elaboratable.ports())
    elif isinstance(elaboratable, wiring.Component):
        ports = [value for _, _, value in elaboratable.signature.flatten(elaboratable)]
    else:
        ports = []
    return ports + list(platform.pins.values())


def to_rtlil(elaboratable, name="top"):
    frag, _, platform = elaborate(elaboratable)
    return rtlil.convert(frag, name=name, ports=ports_of(elaboratable, platform))


def to_verilog(elaboratable, name="top"):
    frag, _, platform = elaborate(elaboratable)
    return verilog.convert(frag, name=name, ports=ports_of(elaboratable, platform))
