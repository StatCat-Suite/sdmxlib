"""Reported reference-metadata queries (#281).

The 13 hand-written sites are overwhelmingly one shape — everything reported
about a given artefact — and they all write it as an inner join from
`metadata_target` to `metadata_value`. Three things follow, and they are what
this file spends its assertions on:

- **The join is a fan-out risk.** `metadata_target` is keyed
  ``(set_urn, ordinal)``, so a set may list the same artefact twice and the
  consumers' join returns each value row twice. The DSL uses a correlated
  ``EXISTS``, and the duplicate-target case is asserted below. This is #254 in
  a different relation.
- **`collect()` must equal `metadata_report`.** The typed view has one
  implementation; the query routes through it rather than re-deriving the MSD
  join, so the two agreeing is a real check that the routing is right.
- **Repeated attributes are sibling rows.** A query keyed only on ``path``
  would silently collapse them, and the SDMX model is explicit that repetition
  is siblings and never a values list.
"""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import artefact_urn
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import q

pytestmark = pytest.mark.unit


def _codelist(cid: str) -> Codelist:
    return Codelist(id=cid, maintainer="AG", version="1.0", name={"en": cid}, codes=[Code(id="A", name={"en": "a"})])


@pytest.fixture
def catalogue():
    """Two targets, two sets, and every value shape the table can hold."""
    with LocalRegistry(":memory:") as reg:
        described = _codelist("CL_DESCRIBED")
        bare = _codelist("CL_BARE")
        msd = MetadataStructure(id="MSD", maintainer="AG", name={"en": "MSD"})
        mdf = Metadataflow(id="MDF", maintainer="AG", name={"en": "MDF"}, structure=msd)
        for a in (described, bare, msd, mdf):
            reg.add(a)

        reg.add(
            MetadataSet(
                id="MDS1",
                maintainer="AG",
                name={"en": "Set one"},
                structure=mdf,
                targets=[artefact_urn(described)],
                attributes=[
                    ReportedAttribute(
                        id="QUALITY",
                        children=[
                            ReportedAttribute(id="COVERAGE", value="national"),
                            ReportedAttribute(id="NOTE", value={"en": "English note", "fr": "Note"}),
                        ],
                    ),
                    ReportedAttribute(id="CONTACT", value="stats@example.org"),
                ],
            )
        )
        yield reg, described, bare


# ── Target anchoring ─────────────────────────────────────────────────────────


def test_filter_by_target_urn_or_artefact(catalogue) -> None:
    reg, described, _ = catalogue
    by_urn = (
        reg.reported_attributes()
        .filter(q.reported_attribute.metadata_set.targets.artefact.urn == str(artefact_urn(described)))
        ._key_rows()
    )
    by_obj = (
        reg.reported_attributes()
        .filter(q.reported_attribute.metadata_set.targets.artefact.id == described.id)
        ._key_rows()
    )
    assert by_urn == by_obj
    assert {k[1] for k in by_urn} == {"QUALITY", "QUALITY.COVERAGE", "QUALITY.NOTE", "CONTACT"}


def test_unknown_target_matches_nothing(catalogue) -> None:
    reg, _, bare = catalogue
    assert (
        reg.reported_attributes().filter(q.reported_attribute.metadata_set.targets.artefact.id == bare.id)._key_rows()
        == []
    )


def test_a_repeated_target_does_not_duplicate_rows(catalogue) -> None:
    """The fan-out the consumers' inner join has and this does not.

    ``metadata_target`` is keyed ``(set_urn, ordinal)``, so the same artefact
    can legitimately appear at two ordinals. A join would then return every
    value of that set twice — silently, and only for the sets that happen to
    be shaped that way. Correlated ``EXISTS`` cannot.
    """
    reg, described, _ = catalogue
    urn = str(artefact_urn(described))
    reg.connection.execute(
        "INSERT INTO sdmx.metadata_target (set_urn, ordinal, target_urn) "
        "SELECT set_urn, 99, target_urn FROM sdmx.metadata_target WHERE target_urn = ?",
        [urn],
    )
    keys = reg.reported_attributes().filter(q.reported_attribute.metadata_set.targets.artefact.urn == urn)._key_rows()
    assert len(keys) == len(set(keys)) == 4


# ── Paths ────────────────────────────────────────────────────────────────────


def test_prefix_selects_the_subtree(catalogue) -> None:
    reg, _, _ = catalogue
    paths = reg.reported_attributes().filter(q.reported_attribute.attribute_path.str.starts_with("QUALITY.")).ids()
    assert paths == ["QUALITY.COVERAGE", "QUALITY.NOTE"]


def test_exact_path(catalogue) -> None:
    reg, _, _ = catalogue
    assert reg.reported_attributes().filter(q.reported_attribute.attribute_path == "CONTACT").ids() == ["CONTACT"]


def test_underscore_in_a_path_is_not_a_wildcard(catalogue) -> None:
    """``_`` is a LIKE metacharacter and SDMX ids are full of them (#222).

    Without escaping, ``starts_with("QUALITY_")`` would match ``QUALITY.``
    — one character, any character — and return the whole subtree. The bug is
    invisible on a fixture whose ids have no underscores, which is why this
    test adds one on purpose.
    """
    reg, _, _ = catalogue
    set_urn = str(artefact_urn(reg.get_all(MetadataSet)[0]))
    reg.connection.execute(
        "INSERT INTO sdmx.metadata_value VALUES (?, 'QUALITY_X', 0, 100, 'scalar', 'literal underscore', NULL)",
        [set_urn],
    )
    assert reg.reported_attributes().filter(q.reported_attribute.attribute_path.str.starts_with("QUALITY_")).ids() == [
        "QUALITY_X"
    ]


# ── Values ───────────────────────────────────────────────────────────────────


def test_scalar_and_localised_values_are_separate_fields(catalogue) -> None:
    """``value`` is the scalar; ``value[lang]`` reaches the localised payload.

    The same distinction `AnnotationRef` makes, and it matters because
    ``value_kind`` decides which column a row populated — a query that read
    only one would silently miss half the rows.
    """
    reg, _, _ = catalogue
    assert reg.reported_attributes().filter(q.reported_attribute.scalar == "national").ids() == ["QUALITY.COVERAGE"]
    assert reg.reported_attributes().filter(q.reported_attribute.value["en"].str.contains("English")).ids() == [
        "QUALITY.NOTE"
    ]
    assert reg.reported_attributes().filter(
        q.reported_attribute.value["en"].str.caseless.contains("english")
    ).ids() == ["QUALITY.NOTE"]
    assert reg.reported_attributes().filter(q.reported_attribute.value["fr"] == "Note").ids() == ["QUALITY.NOTE"]


def test_kind_distinguishes_the_two(catalogue) -> None:
    reg, _, _ = catalogue
    assert reg.reported_attributes().filter(q.reported_attribute.kind == "i18n").ids() == ["QUALITY.NOTE"]


def test_repeated_attributes_are_sibling_rows(catalogue) -> None:
    """Repetition is siblings, never a list — so ``sibling_index`` is a field.

    A query keyed only on ``path`` would return one row where the model says
    there are two, which is the failure this field exists to prevent.
    """
    reg, _, _ = catalogue
    set_urn = str(artefact_urn(reg.get_all(MetadataSet)[0]))
    reg.connection.execute(
        "INSERT INTO sdmx.metadata_value VALUES (?, 'CONTACT', 1, 100, 'scalar', 'second@example.org', NULL)",
        [set_urn],
    )
    contacts = reg.reported_attributes().filter(q.reported_attribute.attribute_path == "CONTACT")._key_rows()
    assert [k[2] for k in contacts] == [0, 1]
    firsts = (
        reg.reported_attributes()
        .filter(q.reported_attribute.attribute_path == "CONTACT")
        .filter(q.reported_attribute.position < 1)
        ._key_rows()
    )
    assert [k[2] for k in firsts] == [0]


# ── collect() agrees with the shipped typed view ─────────────────────────────


def test_collect_equals_metadata_report(catalogue) -> None:
    """The typed view has one implementation; this checks the routing to it.

    `collect` goes through `LocalRegistry.metadata_report` rather than re-deriving
    the MSD join, so a divergence here means the query is filtering or ordering
    differently, not that the typing is wrong. The report is in document order,
    which `ordinal` is (GH-197).
    """
    reg, _, _ = catalogue
    set_urn = artefact_urn(reg.get_all(MetadataSet)[0])
    in_document_order = reg.reported_attributes().order_by(q.reported_attribute.ordinal).collect()
    assert in_document_order == reg.metadata_report(set_urn)
    assert in_document_order != reg.reported_attributes().collect(), "path order differs, so the order is tested"


def test_collect_follows_the_query_order_not_the_report_order(catalogue) -> None:
    """A `MetadataSection` does not carry its set, so order has to be rebuilt.

    The naive implementation returns the report's own ``(path, sibling)``
    ordering and silently discards the caller's ``sort_by``.
    """
    reg, _, _ = catalogue
    descending = reg.reported_attributes().order_by(q.reported_attribute.attribute_path.desc()).collect()
    assert [s.path for s in descending] == ["QUALITY.NOTE", "QUALITY.COVERAGE", "QUALITY", "CONTACT"]


def test_collect_is_filtered(catalogue) -> None:
    reg, _, _ = catalogue
    sections = (
        reg.reported_attributes().filter(q.reported_attribute.attribute_path.str.starts_with("QUALITY.")).collect()
    )
    assert [s.attribute_id for s in sections] == ["COVERAGE", "NOTE"]


# ── Frames and counting ──────────────────────────────────────────────────────


def test_projection(catalogue) -> None:
    reg, _, _ = catalogue
    frame = (
        reg.reported_attributes()
        .select(
            q.reported_attribute.attribute_path, q.reported_attribute.scalar, value_en=q.reported_attribute.value["en"]
        )
        .collect()
    )
    assert frame.columns == ["attribute_path", "scalar", "value_en"]
    assert frame.height == 4


def test_group_by_kind(catalogue) -> None:
    reg, _, _ = catalogue
    counts = {
        r["kind"]: r["len"]
        for r in reg.reported_attributes().group_by(q.reported_attribute.kind).len().collect().to_dicts()
    }
    assert counts["scalar"] == 2
    assert counts["i18n"] == 1


def test_count(catalogue) -> None:
    reg, _, _ = catalogue
    assert reg.reported_attributes().count() == 4


def test_the_metadata_query_does_not_execute_implicitly(catalogue) -> None:
    """#306, and now the generic query's own refusals."""
    reg, _, _ = catalogue
    query = reg.reported_attributes()
    with pytest.raises(TypeError, match=r"not iterable.*\.collect\(\)"):
        iter(query)
    with pytest.raises(TypeError, match=r"no len\(\).*\.count\(\)"):
        len(query)


# ── Discovery from the artefact side ─────────────────────────────────────────


def _described(reg, path: str | None = None, *, subtree: bool = False) -> list[str]:
    """ "Which codelists have metadata reported about them", as a path.

    The replacement for `sl.artefact.has_metadata()`, retired with
    `ArtefactQuery` in #306. `subtree` becomes an ordinary prefix match on the
    attribute path, which is what the predicate compiled to anyway — the
    difference is that it is now visible at the call site.
    """
    described = reg.query(q.artefact).filter(
        q.artefact.type == "Codelist", q.artefact.described_by.metadata_set.id.is_not_null()
    )
    if path is None:
        return sorted(described.ids())
    values = reg.query(q.reported_attribute)
    if subtree:
        values = values.filter(
            (q.reported_attribute.attribute_path == path)
            | q.reported_attribute.attribute_path.str.starts_with(f"{path}.")
        )
    else:
        values = values.filter(q.reported_attribute.attribute_path == path)
    sets = values.select(urn=q.reported_attribute.metadata_set.urn)
    return sorted(described.filter(q.artefact.described_by.metadata_set.urn.is_in(sets)).ids())


def test_has_metadata(catalogue) -> None:
    reg, _, _ = catalogue
    assert _described(reg) == ["CL_DESCRIBED"]


def test_has_metadata_at_a_path(catalogue) -> None:
    reg, _, _ = catalogue
    assert _described(reg, "QUALITY.COVERAGE") == ["CL_DESCRIBED"]
    assert _described(reg, "NOPE") == []


def test_has_metadata_subtree_includes_the_node(catalogue) -> None:
    """A container matches itself and its leaves."""
    reg, _, _ = catalogue
    assert _described(reg, "QUALITY", subtree=True) == ["CL_DESCRIBED"]
    # Without the subtree arm the exact path still matches, because QUALITY is
    # itself a reported row.
    assert _described(reg, "QUALITY") == ["CL_DESCRIBED"]


def test_has_metadata_subtree_escapes_like_metacharacters(catalogue) -> None:
    """``QUALITY_`` must not match ``QUALITY.COVERAGE`` through a wildcard.

    `starts_with` escapes it; the retired predicate had to do the same by hand,
    and this is the test that caught it there.
    """
    reg, _, _ = catalogue
    assert _described(reg, "QUALITY_", subtree=True) == []


def test_agrees_with_metadata_for_target(catalogue) -> None:
    """The artefact-side predicate agrees with the shipped by-target lookup."""
    reg, described, bare = catalogue
    for cl in (described, bare):
        expected = bool(reg.metadata_for_target(artefact_urn(cl)))
        got = cl.id in _described(reg)
        assert got == expected


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_target_cannot_be_projected(catalogue) -> None:
    """A set may have many targets, so there is no single value for the cell.

    The old DSL refused with `NotImplementedError` because `meta.target` was a
    bespoke field it declined to emit. The generic query refuses for the
    *structural* reason instead — the path crosses a to-many edge — and the
    message names the three ways out. A better refusal for the same rule.
    """
    reg, _, _ = catalogue
    with pytest.raises(PathError, match=r"crosses a to-many edge.*\.list\(\)"):
        reg.reported_attributes().select(target=q.reported_attribute.metadata_set.targets.artefact.urn).collect()


def test_the_targets_can_be_collected_instead(catalogue) -> None:
    """What the refusal points at. The old DSL had no spelling for this at
    all — `meta.target` was filter-only."""
    reg, described, _ = catalogue
    frame = (
        reg.reported_attributes()
        .filter(q.reported_attribute.attribute_path == "CONTACT")
        .select(
            path=q.reported_attribute.attribute_path,
            targets=q.reported_attribute.metadata_set.targets.artefact.id.list(),
        )
        .collect()
    )
    assert frame.to_dicts() == [{"path": "CONTACT", "targets": [described.id]}]
