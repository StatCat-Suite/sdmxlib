"""Every public callable's signature evaluates at runtime.

On 3.14 an annotation is evaluated when something asks for it (PEP 649), and
`inspect.signature` asks with `Format.VALUE`. A name imported only under
`if TYPE_CHECKING:` does not exist then, so the signature raises `NameError`:
`inspect.signature(sl.RestRegistry)` did, naming `LocalRegistry`, and with it
Jupyter's tooltips and `?`, and anything that introspects a call (typer,
FastAPI). `help()` hid it, because pydoc falls back to forward references.
Nothing static sees it: pyrefly and mkdocstrings read the source.

So a name an annotation uses is imported for real. `from __future__ import
annotations` is not the fix: it makes every annotation a string, which
`typing.get_type_hints` then cannot resolve against a PEP 695 class parameter.
"""

import importlib.util
import inspect
import types
from collections.abc import Callable
from pathlib import Path

import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

#: `organisation` subclasses `MaintainableArtefact`, so `model/base.py` cannot
#: import `Agency` at runtime. A property getter, which nothing calls by
#: signature.
_CYCLES = {"MaintainableArtefact.maintainer"}


def _callables(namespace: types.ModuleType, names: list[str]) -> list[tuple[str, Callable[..., object]]]:
    found: list[tuple[str, object]] = []
    for name in names:
        obj = getattr(namespace, name)
        found.append((name, obj))
        if inspect.isclass(obj):
            for attr, value in vars(obj).items():
                if attr.startswith("_") and attr != "__init__":
                    continue
                target = value.fget if isinstance(value, property) else getattr(value, "__func__", value)
                found.append((f"{name}.{attr}", target))
    return [(q, obj) for q, obj in found if (inspect.isfunction(obj) or inspect.isclass(obj)) and callable(obj)]


def _unevaluable(namespace: types.ModuleType, names: list[str]) -> set[str]:
    failed: set[str] = set()
    for qualname, obj in _callables(namespace, names):
        try:
            inspect.signature(obj)
        except ValueError:
            continue  # no signature to find, as for a C type
        except NameError, TypeError:
            failed.add(qualname)
    return failed


def test_every_public_signature_evaluates() -> None:
    assert _unevaluable(sl, sl.__all__) == _CYCLES


def test_the_walk_covers_the_public_api() -> None:
    """A floor, not an exact count: the surface grows with every artefact (827 when written)."""
    assert len(_callables(sl, sl.__all__)) >= 800


def test_a_type_checking_only_name_is_caught(tmp_path: Path) -> None:
    """The sabotage check: a signature naming what only a checker imports fails."""
    source = tmp_path / "sabotaged.py"
    source.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from decimal import Decimal\n"
        "def f(x: Decimal) -> None: ...\n"
    )
    spec = importlib.util.spec_from_file_location("sabotaged", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert _unevaluable(module, ["f"]) == {"f"}
