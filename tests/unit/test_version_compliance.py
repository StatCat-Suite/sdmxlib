"""Which SDMX-ML grammar accepts a version, and where that is enforced (#319).

**Neither grammar contains the other**, which is the fact everything here
turns on. 3.0 tightened the numeric form — one or two legacy parts, or exactly
three, no leading zeros — *and* added prereleases:

    both      1.0   1.10   1.0.0   0.0.1
    2.1 only  01.0  1.03   1.2.3.4
    3.0 only  1.0.0-draft  1.0.0-alpha.1

So "convert 2.1 to 3.0" is not always possible and neither is the reverse,
and a single widest-grammar check would accept `1.2.3.4-draft`, which conforms
to nothing.

The enforcement is split, and the split is the design:

* `validate()` takes the **union**, because a store is format-agnostic. It
  holds what a registry published; which format that came from, or is headed
  for, is not its business.
* the **writers** refuse what their own format cannot express, because that is
  the first point at which a format is chosen and the question has an answer.

`test_the_patterns_match_the_vendored_schemas` is what keeps this honest: the
patterns live in `src` as literals — the XSDs are test-only and excluded from
the wheel — so a schema refresh must move the literals with it, or fail here.
"""

from __future__ import annotations

import json
import re

import attrs
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sdmxlib.formats import serialize
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import Agency
from sdmxlib.model.validation import ValidationError
from sdmxlib.model.version import compliance
from tests.support import repo
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

pytestmark = pytest.mark.unit

BOTH = ["1.0", "1.10", "1.0.0", "0.0.1", "1"]
ONLY_21 = ["01.0", "1.03", "1.2.3.4", "1.2.3.4.5"]
ONLY_30 = ["1.0.0-draft", "1.0.0-alpha.1", "1.0.0-rc-1"]
NEITHER = ["1.2.3.4-draft", "", "v1.0", "1.0.0-"]


def _codelist(version: str) -> Codelist:
    return Codelist(id="CL", maintainer=Agency(id="AG"), name={"en": "CL"}, version=version)


# ── the classification ────────────────────────────────────────────────────


@pytest.mark.parametrize("version", BOTH)
def test_versions_both_grammars_accept(version: str) -> None:
    assert compliance(version) == {"2.1", "3.0"}


@pytest.mark.parametrize("version", ONLY_21)
def test_versions_only_2_1_accepts(version: str) -> None:
    """2.1 permits unlimited parts and leading zeros; 3.0 does not."""
    assert compliance(version) == {"2.1"}


@pytest.mark.parametrize("version", ONLY_30)
def test_versions_only_3_0_accepts(version: str) -> None:
    """3.0 added the semantic prerelease; 2.1 has no notion of it."""
    assert compliance(version) == {"3.0"}


@pytest.mark.parametrize("version", NEITHER)
def test_versions_neither_grammar_accepts(version: str) -> None:
    assert compliance(version) == frozenset()


def test_neither_grammar_contains_the_other() -> None:
    """The claim the whole design rests on, asserted rather than assumed.

    If one were a superset, `validate()` could simply use it and the writers
    would need no check at all.
    """
    assert ONLY_21, "2.1 must accept something 3.0 does not"
    assert ONLY_30, "3.0 must accept something 2.1 does not"


def test_the_patterns_match_the_vendored_schemas() -> None:
    """Derived from the XSDs, so a schema refresh cannot silently diverge.

    The patterns are literals in `src` because the schemas are test-only and
    excluded from the wheel — so this is the only thing tying the two
    together. Every version either schema's own pattern accepts must be
    classified under that schema's name here, and vice versa.
    """
    schemas = repo.ROOT / "tests" / "schemas"
    patterns: dict[str, re.Pattern[str]] = {}

    text_21 = (schemas / "sdmx_ml21" / "SDMXCommonReferences.xsd").read_text(errors="ignore")
    body = re.search(r'<xs:simpleType name="VersionType">(.*?)</xs:simpleType>', text_21, re.DOTALL)
    assert body is not None, "2.1 VersionType not found — has the schema been refreshed?"
    found = re.search(r'<xs:pattern value="([^"]+)"', body.group(1))
    assert found is not None
    patterns["2.1"] = re.compile(f"^(?:{found.group(1)})$")

    text_30 = (schemas / "sdmx_ml30" / "SDMXCommonReferences.xsd").read_text(errors="ignore")
    members = []
    for name in ("LegacyVersionNumberType", "SemanticVersionNumberType"):
        part = re.search(rf'<xs:simpleType name="{name}">(.*?)</xs:simpleType>', text_30, re.DOTALL)
        assert part is not None, f"3.0 {name} not found — has the schema been refreshed?"
        pattern = re.search(r'<xs:pattern value="([^"]+)"', part.group(1))
        assert pattern is not None
        members.append(pattern.group(1))
    patterns["3.0"] = re.compile("|".join(f"^(?:{m})$" for m in members))

    for version in [*BOTH, *ONLY_21, *ONLY_30, *NEITHER]:
        from_schema = frozenset(name for name, pattern in patterns.items() if pattern.match(version))
        assert compliance(version) == from_schema, version


# ── validate() takes the union ────────────────────────────────────────────


@pytest.mark.parametrize("version", [*BOTH, *ONLY_21, *ONLY_30])
def test_the_store_accepts_anything_either_grammar_allows(version: str) -> None:
    """A store is format-agnostic, so refusing a conformant 3.0 artefact for
    failing 2.1's grammar — the previous behaviour — made it unstorable."""
    _codelist(version)


@pytest.mark.parametrize("version", ["1.2.3.4-draft", "v1.0"])
def test_the_store_still_refuses_what_conforms_to_nothing(version: str) -> None:
    """The union is looser than either grammar and that is the trade; it is
    not *unbounded*."""
    with pytest.raises(ValidationError, match="conforms to no SDMX-ML version"):
        _codelist(version)


# ── the writers refuse what they cannot express ───────────────────────────


def _write(module_name: str, version: str) -> None:
    from importlib import import_module

    writer = import_module(f"sdmxlib.formats.{module_name}.writer")
    writer.write(StructureMessage(codelists=[_codelist(version)]))


@pytest.mark.parametrize("version", ONLY_30)
def test_a_3_0_only_version_is_left_out_of_2_1(version: str) -> None:
    """Previously this emitted XML that failed XSD validation — caught by a
    round-trip test, never by a user — and then raised. A 3.0-only version
    is a draft, and 2.1 now leaves the artefact out (#424): stripping the
    extension would make a draft look final. `test_drafts_out_of_2_1` has
    the rest."""
    from sdmxlib.formats.sdmx_ml21 import reader, writer

    assert not reader.read(writer.write(StructureMessage(codelists=[_codelist(version)]))).codelists


@pytest.mark.parametrize("version", ONLY_21)
def test_a_2_1_only_version_cannot_be_written_as_3_0(version: str) -> None:
    for module_name in ("sdmx_ml30", "sdmx_ml31"):
        with pytest.raises(ValidationError, match=re.escape("SDMX-ML 3.0")):
            _write(module_name, version)


@pytest.mark.parametrize("version", BOTH)
def test_a_version_both_accept_writes_everywhere(version: str) -> None:
    for module_name in ("sdmx_ml21", "sdmx_ml30", "sdmx_ml31", "sdmx_json"):
        _write(module_name, version)


# ── SDMX-JSON uses the 3.0 grammar (#417) ─────────────────────────────────
#
# The JSON structure writer checked nothing, on the belief — written in a
# comment and in #188's closing note — that its schema "states no pattern at
# all". It does: `VersionType` is an `anyOf` over SDMX-ML 3.0's two patterns,
# under a `description` still carrying 2.1's prose. So `1.2.3.4` and `1.03`
# were written schema-invalid.


@pytest.mark.parametrize("version", ONLY_21)
def test_a_2_1_only_version_cannot_be_written_as_json(version: str) -> None:
    with pytest.raises(ValidationError, match=re.escape("as SDMX-JSON")):
        _write("sdmx_json", version)


@pytest.mark.parametrize("version", ONLY_30)
def test_a_3_0_only_version_writes_as_json(version: str) -> None:
    _write("sdmx_json", version)


def _json_version_patterns() -> list[re.Pattern[str]]:
    """`VersionType`'s patterns, read out of the vendored structure schema."""
    found: list[re.Pattern[str]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "VersionType" and isinstance(value, dict):
                    found.extend(re.compile(str(alt["pattern"])) for alt in value["anyOf"])
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(
        json.loads(
            (repo.ROOT / "tests" / "schemas" / "sdmx_json" / "2.1" / "sdmx-json-structure-schema.json").read_text()
        )
    )
    return found


_JSON_PATTERNS = _json_version_patterns()


def _json_writer_accepts(version: str) -> bool:
    try:
        _write("sdmx_json", version)
    except ValidationError:
        return False
    return True


def test_the_json_schema_really_constrains_the_version() -> None:
    """The premise, asserted: two patterns, and they refuse what 2.1 allows."""
    patterns = _json_version_patterns()
    assert len(patterns) == 2, "VersionType changed shape — has the schema been refreshed?"
    assert not any(p.match("1.2.3.4") for p in patterns)


@given(version=st.text(alphabet="0123456789.-ar", max_size=12))
def test_the_json_writer_accepts_exactly_what_the_schema_does(version: str) -> None:
    """Differential, against the schema's own patterns rather than `compliance`.

    `compliance` is what the writer calls, so comparing the two would compare
    the code with itself; the vendored schema is the independent oracle.
    """
    schema_accepts = any(p.match(version) for p in _JSON_PATTERNS)
    assert _json_writer_accepts(version) == schema_accepts, version


@pytest.mark.parametrize("version", [*BOTH, *ONLY_30])
def test_what_the_json_writer_accepts_is_schema_valid(version: str) -> None:
    """The other direction, end to end: an accepted version yields a valid document."""
    from sdmxlib.formats.sdmx_json.writer import write

    assert_valid_sdmx_json(write(StructureMessage(codelists=[_codelist(version)])), compile_json_schema("structure"))


def test_the_refusal_names_the_other_format_as_the_fix() -> None:
    """ "Re-version, or write the other format" is the whole set of fixes, so
    the message states both rather than only the problem (#233, #305)."""
    with pytest.raises(ValidationError) as caught:
        _write("sdmx_ml30", "1.03")
    message = str(caught.value)
    assert "1.03" in message
    assert "SDMX-ML 2.1" in message, "the format that *can* express it"
    assert "re-version" in message


def test_a_version_refusal_is_a_validation_error_naming_the_field() -> None:
    """A refusal is a refusal: one `except ValidationError` covers "cannot
    write this", and `.issues` names the field, like every other (#420).

    It was a separate `ValidationError(ValueError)` until then, on the
    reasoning that a version unconvertible to one format is no defect in the
    artefact. True, and no different from a multi-measure DSD refused for
    2.1 — which was always a `ValidationError`. The two rules met in the wire
    gate over populated artefacts, which had to accept both.
    """
    with pytest.raises(ValidationError) as caught:
        _write("sdmx_ml30", "1.03")
    assert [issue.path for issue in caught.value.issues] == ["Codelist:CL.version"]


def test_serialize_refuses_every_bad_version_at_once_before_writing() -> None:
    """`check_serialisable` asks first, accumulated across the message, so a
    caller sees every unwritable version together rather than the first one
    a writer happened to reach."""
    message = StructureMessage(codelists=[_codelist("1.2.3.4"), attrs.evolve(_codelist("1.03"), id="CL2")])
    with pytest.raises(ValidationError) as caught:
        serialize(message, version="3.1")
    assert [issue.path for issue in caught.value.issues] == ["Codelist:CL.version", "Codelist:CL2.version"]
    assert all("SDMX-ML 3.1" in issue.message for issue in caught.value.issues)
