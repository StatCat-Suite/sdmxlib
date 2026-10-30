"""Which extended codelist wins a code clash: FMR's answer, replayed (#486).

The IM says the later one (§4.3.3.2: "The latest Codelist used overrides any
previous Codelist"), and the XSDs give no direction. `apply_extensions` let the
earlier one win, and its docstring credited FMR for that. Asked directly, FMR
12.4.2 does what the IM says. It flattens an extending codelist when it serves
SDMX-ML 2.1, which has no extensions, so its 2.1 response shows which code won.

`sent.xml` is the message built below, as sent. `back/` holds FMR's 2.1
response for each extending codelist.
"""

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.model.codelist import Code, Codelist, CodelistExtension
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support.fmr_oracle import RECORDINGS

pytestmark = pytest.mark.integration

_CASE = RECORDINGS / "extension-precedence"


def _codelist(cl_id: str, name: str, codes: list[Code], extends: tuple[Codelist, ...] = ()) -> Codelist:
    return Codelist(
        id=cl_id,
        maintainer="AG",
        name={"en": name},
        codes=codes,
        extensions=[CodelistExtension(codelist=Ref.to(source)) for source in extends],
    )


_X1 = _codelist("CL_X1", "x1", [Code(id="A", name={"en": "A from X1"}), Code(id="B", name={"en": "B from X1"})])
_X2 = _codelist("CL_X2", "x2", [Code(id="A", name={"en": "A from X2"}), Code(id="C", name={"en": "C from X2"})])

#: CL_E12 extends X1 then X2 and has its own B; CL_E21 extends X2 then X1.
_MESSAGE = StructureMessage(
    codelists=[
        _X1,
        _X2,
        _codelist("CL_E12", "CL_E12", [Code(id="B", name={"en": "B own"})], extends=(_X1, _X2)),
        _codelist("CL_E21", "CL_E21", [], extends=(_X2, _X1)),
    ]
)


def _names(codelist: Codelist) -> dict[str, str]:
    return {code.id: code.name["en"].text for code in codelist.codes}


def _served(cl_id: str) -> dict[str, str]:
    """The codes FMR served for `cl_id`, flattened, by id."""
    served = parse((_CASE / "back" / f"{cl_id}-ml21.xml").read_bytes())
    assert isinstance(served, StructureMessage)
    return _names(served.codelists[cl_id])


def test_what_was_sent_is_what_we_send_now() -> None:
    """A writer or model change that alters the document fails here, so the recording is re-made."""
    assert serialize(_MESSAGE, version="3.0", pretty_print=True) == (_CASE / "sent.xml").read_bytes()


@pytest.mark.parametrize("cl_id", ["CL_E12", "CL_E21"])
def test_applied_extensions_match_what_fmr_serves(cl_id: str) -> None:
    applied = _MESSAGE.codelists[cl_id].apply_extensions(_MESSAGE.codelists)
    assert _names(applied) == _served(cl_id)


def test_the_recording_discriminates() -> None:
    """FMR's answers differ on A between the two orders, so the comparison can tell the precedences apart."""
    assert _served("CL_E12") == {"A": "A from X2", "B": "B own", "C": "C from X2"}
    assert _served("CL_E21") == {"A": "A from X1", "B": "B from X1", "C": "C from X2"}
