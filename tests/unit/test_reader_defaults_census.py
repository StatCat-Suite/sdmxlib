"""Every attribute an SDMX-ML reader reads with a default, and why that default (#474).

``el.get("id", "")`` reads a required attribute as if the document had stated
an empty one. #474 found that pattern behind a link with no url, a maintainable
that failed the whole document, and a 2.1 reference that silently vanished, and
replaced it with `required`. What is left reads a default for a reason, and
each reason is checked rather than trusted:

* the schema's own default or fixed value — found in the vendored XSDs;
* an assumption the read records — found in `ASSUMPTIONS`;
* a known defect — named by its issue, so fixing one fails here until its row
  goes.

The count is exact: a new defaulted read fails until it is classified here.
"""

import ast
import collections
import functools

import pytest
from lxml import etree

from sdmxlib.formats.sdmx_ml._losses import ASSUMPTIONS
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_FORMATS = ROOT / "src" / "sdmxlib" / "formats"

#: Each reader module, and the SDMX-ML versions it reads.
_MODULES = {
    "sdmx_ml/_common.py": ("2.1", "3.0", "3.1"),
    "sdmx_ml/_v3_reader.py": ("3.0", "3.1"),
    "sdmx_ml21/reader.py": ("2.1",),
}

_SCHEMA = "the schema's default or fixed value"
_ASSUMED = "assumed, and recorded on StructureMessage.assumed"
#: Optional with no schema default. The model holds a `bool`, not `bool | None`,
#: and every writer omits a false one, so absent and false are one value on the
#: wire and in the model alike.
_UNSTATED_BOOLEAN = "optional, no schema default: absent and false are one value, written as absent"
_SCHEMA_21_ASSUMED_3X = "2.1's schema default; assumed in 3.x, which reads its absence as un-versioned"

#: (module, attribute, default as written) → how many reads, and why.
_CENSUS: dict[tuple[str, str, str], tuple[int, str]] = {
    ("sdmx_ml/_common.py", "include", "'true'"): (3, _SCHEMA),
    # `xs:boolean`s read through `flag`, which until #458 this census could not see.
    ("sdmx_ml/_common.py", "isExternalReference", "'false'"): (1, _SCHEMA),
    ("sdmx_ml/_common.py", "isPartial", "'false'"): (1, _SCHEMA),
    ("sdmx_ml/_common.py", "isPartialLanguage", "'false'"): (1, _SCHEMA),
    ("sdmx_ml/_common.py", "removePrefix", "'false'"): (2, _UNSTATED_BOOLEAN),
    ("sdmx_ml/_common.py", "isFinal", "'false'"): (1, _SCHEMA),
    ("sdmx_ml/_common.py", "isInclusive", "'true'"): (1, _SCHEMA),
    # Nine maintainables' own versions, and one 2.1 reference's (#476).
    ("sdmx_ml/_common.py", "version", "'1.0'"): (10, _SCHEMA_21_ASSUMED_3X),
    ("sdmx_ml/_common.py", "maintainableParentVersion", "'1.0'"): (1, _SCHEMA),
    ("sdmx_ml/_common.py", "type", "absent"): (1, "#475: 2.1's schema default; 3.0's role, assumed"),
    ("sdmx_ml/_common.py", "id", "''"): (1, "names the constraint in an error message, never a value"),
    # Not the three organisation schemes: 3.x prohibits their @version (#458).
    ("sdmx_ml/_v3_reader.py", "version", "'1.0'"): (9, _ASSUMED),
    ("sdmx_ml/_v3_reader.py", "evolvingStructure", "'false'"): (1, _SCHEMA),
    ("sdmx_ml/_v3_reader.py", "isRegEx", "'false'"): (1, _UNSTATED_BOOLEAN),
    ("sdmx_ml/_v3_reader.py", "optional", "'false'"): (1, _SCHEMA),
    ("sdmx_ml/_v3_reader.py", "isPresentational", "'false'"): (1, _SCHEMA),
    ("sdmx_ml/_v3_reader.py", "id", "'TIME_PERIOD'"): (1, _SCHEMA),
    ("sdmx_ml/_v3_reader.py", "epochPeriod", "'day'"): (1, _ASSUMED),
    ("sdmx_ml/_v3_reader.py", "hasFormalLevels", "'false'"): (1, _ASSUMED),
    # Names a dimension in a position refusal; a component's own id is its
    # concept's when omitted (`component_id`, #477).
    ("sdmx_ml/_v3_reader.py", "id", "''"): (1, "names a dimension in an error message, never a value"),
    ("sdmx_ml21/reader.py", "version", "'1.0'"): (9, _SCHEMA),
    ("sdmx_ml21/reader.py", "isExtension", "'false'"): (1, _SCHEMA),
    ("sdmx_ml21/reader.py", "isPresentational", "'false'"): (1, _SCHEMA),
    ("sdmx_ml21/reader.py", "leveled", "'false'"): (1, _SCHEMA),
    ("sdmx_ml21/reader.py", "id", "'TIME_PERIOD'"): (1, _SCHEMA),
    ("sdmx_ml21/reader.py", "id", "'OBS_VALUE'"): (1, _SCHEMA),
    ("sdmx_ml21/reader.py", "id", "REPORTING_YEAR_START_DAY"): (
        1,
        "ReportingYearStartDay's id, fixed by the schema (#422)",
    ),
    # A hierarchical code's parent version, `HierarchicalCodeRefType`'s 1.0
    # by default; `@version` first, tolerated where producers put it (#476).
    ("sdmx_ml21/reader.py", "maintainableParentVersion", "ref_el.get('version', '1.0')"): (
        1,
        "the XSD's default, 1.0, behind a tolerated @version",
    ),
    # A hierarchical code's 2.1 alias form names its codelist by alias, not
    # by agency and parent; the empty value selects that branch.
    ("sdmx_ml21/reader.py", "agencyID", "''"): (1, "the alias form's discriminant"),
    ("sdmx_ml21/reader.py", "maintainableParentID", "''"): (1, "the alias form's discriminant"),
}


def _flag_default(node: ast.Call) -> str | None:
    """A `flag(el, "<attribute>")` read's default, written as a ``get`` would write it.

    `flag` reads every `xs:boolean` (#458), so its default is as much a
    defaulted read as ``el.get("leveled", "false")`` was — and unless counted
    here, moving a read into it would take the read out of this census.
    """
    if not (isinstance(node.func, ast.Name) and node.func.id == "flag"):
        return None
    default = next((k.value for k in node.keywords if k.arg == "default"), ast.Constant(value=False))
    return repr(str(ast.literal_eval(default)).lower())


def _defaulted_reads(module: str) -> collections.Counter[tuple[str, str, str]]:
    """Every ``x.get("<attribute>", <default>)`` or `flag` read in `module` whose default is not None."""
    found: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for node in ast.walk(ast.parse((_FORMATS / module).read_text())):
        if isinstance(node, ast.Call) and (flagged := _flag_default(node)) is not None:
            attribute = node.args[1]
            assert isinstance(attribute, ast.Constant)
            assert isinstance(attribute.value, str)
            found[module, attribute.value, flagged] += 1
            continue
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            continue
        if len(node.args) != 2:
            continue  # a one-argument get has no default
        key, default = node.args
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and not _is_none(default):
            found[module, key.value, ast.unparse(default)] += 1
    return found


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def test_every_defaulted_read_is_classified() -> None:
    found: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for module in _MODULES:
        found += _defaulted_reads(module)
    assert dict(found) == {key: count for key, (count, _) in _CENSUS.items()}


def test_the_walk_sees_a_defaulted_read() -> None:
    """The sabotage check: the walk finds what it is looking for, so a match is not vacuous."""
    tree = ast.parse('el.get("id", "")\nel.get("id")\nel.get("id", None)')
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert [len(c.args) == 2 and not _is_none(c.args[1]) for c in calls] == [True, False, False]


@functools.cache
def _xsd_defaults(version: str) -> frozenset[tuple[str, str]]:
    """Every (attribute, value) an XSD of `version` declares as a default or fixed value."""
    ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
    found: set[tuple[str, str]] = set()
    for path in (ROOT / "tests" / "schemas" / f"sdmx_ml{version.replace('.', '')}").glob("*.xsd"):
        for attribute in etree.parse(path).iterfind(".//xs:attribute[@name]", namespaces=ns):
            for value in (attribute.get("default"), attribute.get("fixed")):
                if value is not None:
                    found.add((attribute.get("name", ""), value))
    return frozenset(found)


def _versions_declaring(attribute: str, value: object, versions: tuple[str, ...]) -> set[str]:
    return {v for v in versions if (attribute, value) in _xsd_defaults(v)}


def _versions_assuming(attribute: str, value: object) -> set[str]:
    return {
        v
        for a in ASSUMPTIONS
        if a.construct.rsplit("/", maxsplit=1)[-1] == f"@{attribute}" and a.assumed == value
        for v in a.versions
    }


@pytest.mark.parametrize(
    ("module", "attribute", "default"),
    [key for key, (_, why) in _CENSUS.items() if why == _SCHEMA],
)
def test_a_schema_default_is_the_schemas(module: str, attribute: str, default: str) -> None:
    value = ast.literal_eval(default)
    assert _versions_declaring(attribute, value, _MODULES[module]), (
        f"no {_MODULES[module]} XSD declares @{attribute} default or fixed {value!r}"
    )


@pytest.mark.parametrize(
    ("module", "attribute", "default"),
    [key for key, (_, why) in _CENSUS.items() if why == _ASSUMED],
)
def test_an_assumed_default_is_declared(module: str, attribute: str, default: str) -> None:
    assert _versions_assuming(attribute, ast.literal_eval(default)) >= set(_MODULES[module])


def test_a_shared_version_default_is_2_1s_schema_and_3_xs_assumption() -> None:
    assert _versions_declaring("version", "1.0", ("2.1", "3.0", "3.1")) == {"2.1"}
    assert _versions_assuming("version", "1.0") == {"3.0", "3.1"}
