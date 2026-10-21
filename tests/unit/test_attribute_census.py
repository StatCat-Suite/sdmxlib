"""Every attribute SDMX-ML allows has an owner: the writer, the loss register, or the schema (#422).

The element census (`test_element_census.py`) compares the child *elements*
a schema allows with what the writer emits, and it is what found most of the
register's rows. Attributes were outside it, and so outside every guard: an
item scheme's `isPartial` and a maintainable's `isExternalReference` and its
URLs (both held since), a text format's `timeInterval`, a measure's `usage` — read by nobody,
dropped with nothing reported. This census is that comparison for attributes.

Per version, it compares two sets of ``(element, attribute)`` pairs:

- what the schema allows, from `tests.support.xsd_attributes` — keyed by
  element name as the closed-world table is;
- what sdmxlib's writer puts on each element of the maximal fixture and the
  version's shapes.

Every pair the schema allows and the writer never emits, on an element the
writer does emit, is named below as one of four things:

- **loss** — a construct with a `LOSSES` row, so a read reports it;
- **derived** — something the model computes rather than holds, so there is
  nothing to keep: a URN, from the identity it names;
- **fixed** — an attribute the schema fixes, which carries no information;
- **absent** — a pair no valid document holds: the table is keyed by element
  *name*, so it over-allows where two types share one.

An attribute the reader *does* hold is written by a fixture instead, and so
never appears here. Checked in both directions, as the element census is.
"""

from collections.abc import Callable

import pytest
from lxml import etree

from sdmxlib.formats.sdmx_ml._allowed_children import NAMESPACES
from sdmxlib.formats.sdmx_ml._losses import LOSSES
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.message import StructureMessage
from tests.support import maximal, shapes
from tests.support.header import ATTRIBUTES as HEADER_ATTRIBUTES
from tests.support.header import header_only
from tests.support.xsd import SchemaVersion
from tests.support.xsd_attributes import allowed_attributes

pytestmark = pytest.mark.unit

_WRITERS: dict[SchemaVersion, Callable[[StructureMessage], bytes]] = {
    "2.1": ml21_writer.write,
    "3.0": ml30_writer.write,
    "3.1": ml31_writer.write,
}
_SHAPES: dict[SchemaVersion, Callable[[], StructureMessage]] = {
    "2.1": shapes.message_21,
    "3.0": shapes.message,
    "3.1": shapes.message_31,
}
_XML = "{http://www.w3.org/XML/1998/namespace}"


#: On any element: the identity is the model's, and the URN is built from it.
_BY_ATTRIBUTE = {"urn": "derived: a URN, which sdmxlib builds from the identity it names"}

_FIXED_KEY = "fixed: a data key's `include` is fixed true"

#: Every pair the 2.1 schema allows and the writer does not emit, classified.
_UNWRITTEN_21: dict[tuple[str, str], str] = {
    **{
        (f"str:{scheme}", "isFinal"): "fixed: an organisation scheme's `isFinal` is fixed false"
        for scheme in ("AgencyScheme", "DataProviderScheme", "DataConsumerScheme")
    },
    **{
        (f"str:{dimension}", "type"): "derived: the element's own name, which the attribute restates"
        for dimension in ("Dimension", "MeasureDimension", "TimeDimension")
    },
    ("Ref", "local"): "derived: whether a reference is local, which its shape says",
    ("str:IncludedCodelist", "alias"): (
        "alternative: the alias the alias form of a hierarchical code names, read by `test_hcl_alias_form.py`"
    ),
    ("str:Key", "include"): _FIXED_KEY,
    **{
        ("str:Key", attribute): "absent: a metadata key's, which shares the element name; a data key has none"
        for attribute in ("metadataTarget", "report")
    },
}

#: Every pair the 3.0 schema allows and the writer does not emit, classified.
_UNWRITTEN_30: dict[tuple[str, str], str] = {
    ("str:Key", "include"): _FIXED_KEY,
    **{
        ("str:Component", attribute): (
            "absent: `str:Component` names a data key's component, which takes them, and a cube region's, "
            "whose type prohibits validity and which the abstract head's uri never reaches"
        )
        for attribute in ("uri", "validFrom", "validTo")
    },
}

#: 3.1: 3.0's — the two flags 3.1 added are held (#422).
_UNWRITTEN_31: dict[tuple[str, str], str] = dict(_UNWRITTEN_30)

_UNWRITTEN: dict[SchemaVersion, dict[tuple[str, str], str]] = {
    "2.1": _UNWRITTEN_21,
    "3.0": _UNWRITTEN_30,
    "3.1": _UNWRITTEN_31,
}


def _documents(version: SchemaVersion) -> list[etree._Element]:
    write = _WRITERS[version]
    return [etree.fromstring(write(message)) for message in (maximal.message(), _SHAPES[version]())]


def _name(el: etree._Element, prefixes: dict[str, str]) -> str:
    qname = etree.QName(el)
    return f"{prefixes[qname.namespace]}:{qname.localname}" if qname.namespace else qname.localname


def _unwritten(version: SchemaVersion, documents: list[etree._Element] | None = None) -> set[tuple[str, str]]:
    """The allowed pairs the documents never hold, on an element they do hold."""
    prefixes = {uri: prefix for prefix, uri in NAMESPACES[version].items()}
    written: dict[str, set[str]] = {}
    for doc in _documents(version) if documents is None else documents:
        for el in doc.iter():
            if isinstance(el.tag, str):
                attributes = {f"xml:{a.removeprefix(_XML)}" if a.startswith(_XML) else a for a in el.attrib}
                written.setdefault(_name(el, prefixes), set()).update(attributes)
    allowed = allowed_attributes(version)
    return {
        (element, attribute)
        for element, held in written.items()
        for attribute in allowed.get(element, frozenset()) - held
    }


@pytest.mark.parametrize("version", sorted(_WRITERS))
def test_every_unwritten_attribute_is_classified(version: SchemaVersion) -> None:
    # Under the header, exactly what a structure message never holds (#480).
    header = {pair for pair in _unwritten(version) if pair[0] in header_only(version)}
    assert header == set(HEADER_ATTRIBUTES), "the header's unwritten pairs changed: classify them"
    unwritten = _unwritten(version) - header
    table = _UNWRITTEN[version]
    unclassified = sorted(p for p in unwritten - set(table) if p[1] not in _BY_ATTRIBUTE)
    assert not unclassified, (
        f"SDMX-ML {version} allows these, and nothing here writes them or says why: "
        + "".join(f"\n    {element} @{attribute}" for element, attribute in unclassified)
        + "\n\nInject one into the maximal message and read it: if it is held, write it in a fixture; "
        "if it is dropped, give it a `LOSSES` row; if it carries nothing, say so here."
    )
    stale = sorted(set(table) - unwritten)
    assert not stale, f"these are written now, or no longer allowed — delete them: {stale}"
    for attribute in _BY_ATTRIBUTE:
        assert any(a == attribute for _, a in unwritten), f"no element leaves @{attribute} unwritten — drop the rule"


@pytest.mark.parametrize("version", sorted(_WRITERS))
def test_every_loss_named_here_is_a_registered_row(version: SchemaVersion) -> None:
    rows = {loss.construct for loss in LOSSES if version in loss.versions}
    named = {kind.removeprefix("loss: ") for kind in _UNWRITTEN[version].values() if kind.startswith("loss: ")}
    assert named <= rows, f"no LOSSES row for {sorted(named - rows)}"


def test_the_census_reads_the_schema_attributes() -> None:
    """The walk reaches what the XSDs say: a sample read straight off them, inherited and referenced alike."""
    for version in ("2.1", "3.0", "3.1"):
        allowed = allowed_attributes(version)
        # Inherited from `MaintainableType` through two derivations.
        assert {"isExternalReference", "serviceURL", "structureURL", "version"} <= allowed["str:Codelist"]
        # A referenced attribute keeps its namespace.
        assert allowed["com:Name"] == {"xml:lang"}
    assert "isFinal" in allowed_attributes("2.1")["str:Codelist"]
    assert "isFinal" not in allowed_attributes("3.0")["str:Codelist"], "3.0 removed it"


def test_the_census_can_find_a_drop() -> None:
    """Discrimination: an attribute the writer stops emitting is reported as unclassified."""
    documents = _documents("3.0")
    for doc in documents:
        for code in doc.iter(f"{{{NAMESPACES['3.0']['str']}}}Codelist"):
            code.attrib.pop("validFrom", None)
    assert ("str:Codelist", "validFrom") not in _unwritten("3.0")
    assert ("str:Codelist", "validFrom") in _unwritten("3.0", documents) - set(_UNWRITTEN_30)
