"""DuckDB schema for SDMX structural metadata (v2 — pure relational).

All tables live in the ``sdmx`` DuckDB schema.  Item tables use
``MAP(VARCHAR, VARCHAR)`` columns for multilingual labels — no upfront
language config, no DDL migration.  Artefact-level text uses a
``localized_text`` table.

Example:
    import duckdb
    from sdmxlib.storage.schema import create_schema

    conn = duckdb.connect("store.duckdb")
    cursor = create_schema(conn)
    # cursor has search_path = 'sdmx'
"""

import duckdb

from sdmxlib._duckdb import first

#: 2.18 adds the `hierarchy_applicability` view (#372) — the scoped-beats-
#: unscoped resolution rule, stated once in the DDL so the Python resolver,
#: the path language and a downstream consumer all read the same answer.
#:
#: 2.19 adds ``annotation.owner_artefact_urn`` (#145) — the maintainable an
#: annotation's owner belongs to, so the delete cascade can find one
#: artefact's annotations by an indexed equality instead of the
#: ``LIKE '%=SUFFIX.%'`` an item URN's shape forced. A store on 2.18 has the
#: rows but not the column, and nothing can backfill it from `owner_urn`
#: alone without re-deriving the URN grammar, so this is a rebuild — which
#: costs nothing, the store being a cache.
#:
#: 2.17 stores an absent reference as NULL. It was the empty string, and the
#: `NOT NULL` on eight of these columns was enforcing nothing as a result —
#: every one of the model fields behind them is `Optional`, so "required" was
#: a constraint the writer satisfied by smuggling absence through as `''`.
#: The cost was visible in the query language: asking for absence found
#: nothing, because `is_null()` on a dataflow with no structure, or on an
#: unscoped HierarchyAssociation, compared a NULL that was never written.
#:
#: 2.16 splits ``hierarchy_association.linked_object_urn`` into the two halves
#: a join needs (#372). An HA usually links a *Dimension*, whose item URN is
#: not in ``sdmx.artefact`` — only maintainables are — so the declared edge to
#: the spine matched nothing and there was no path from an association to the
#: component it links. Downstream that forced string surgery on the URN
#: grammar; the parsed columns are baked here instead, where the grammar
#: already lives.
#:
#: 2.13 drops ``dsd_component.related_measure`` (#360) — the column only ever
#: held a constant or a fabrication, so nothing is lost by rebuilding without
#: it. A store on 2.12 is otherwise readable, which is exactly why the bump
#: matters: neither the SELECT nor the INSERT names the column any more, so
#: without a version move the dead column would sit there unrebuilt.
#:
#: 2.20 adds ``region_selection`` (#433): a cube region's keys and component
#: value sets, one row each, with their time ranges. A key with a time range
#: has no values, so ``region_key_value`` alone lost it — and every key with
#: no values, and the keys' order.
#:
#: 2.21 adds ``data_key`` and ``data_key_value`` (#437): a constraint's
#: explicit observation keys, which the store had no table for and dropped.
#:
#: 2.22 adds the ``enum_*`` column group to ``dsd_component`` and
#: ``metadata_attribute`` (#438): a component's `enumeration_format`, which
#: had no column and was dropped.
#:
#: 2.23 adds a ``uri`` column to every item and component table, and
#: ``valid_from`` / ``valid_to`` to ``value_map`` (#442). The model carries
#: `uri` on every identifiable, and the store kept it on the artefact spine
#: alone — so a code, a dimension or a hierarchy node lost its link on a
#: store/load round-trip, as did a value map's validity.
#: 2.24 keys nested items by path (#439): ``category``, ``hierarchy_node`` and
#: ``metadata_attribute`` gain ``path`` and ``ancestor_paths``, their parent
#: column becomes ``parent_path``, uniqueness moves from the leaf id to the
#: path, and the item URN carries the path. ``hierarchy_node`` gains the
#: ``position`` the other two already had.
#:
#: 2.25 derives finality from the version (#420): ``artefact.is_final`` is
#: baked from it, and the new ``legacy_is_final`` keeps a 2.1 ``isFinal``
#: that disagrees with it, NULL otherwise.
#:
#: 2.26 adds ``version_rewrite`` (#420): the version an artefact was published
#: at, for each one `add_all(rewrite_version=...)` ingested at another.
#:
#: 2.27 adds SDMX 3.0's DSD component additions (#422): ``dsd_component``
#: gains ``optional_dimensions``, ``related_measures`` (NULL: all measures),
#: ``min_occurs`` and ``max_occurs`` (NULL: unbounded — so both default to 1,
#: which also fills the rows of a store migrated in place), and the new
#: ``dsd_metadata_attribute`` holds a DSD's metadata attribute usages. The
#: DSD's MSD is an ``artefact_ref`` with role ``metadata``.
#:
#: 2.28 adds ``is_included`` to ``region_selection`` and ``region_key_value``
#: (#422): a value set's own ``include``, which excludes its values when
#: false. Kept on each value row as well, so a query over
#: ``region.key_values`` can tell an allowed value from an excluded one
#: without a join. Default true, which also fills a store migrated in place.
#:
#: 2.29 holds a region value's and value set's qualifiers (#422):
#: ``region_key_value`` gains ``cascade``, ``lang``, ``valid_from`` and
#: ``valid_to``; ``region_selection`` gains ``remove_prefix`` (default false)
#: and ``valid_from`` / ``valid_to``. Periods as written, like the time range's.
#:
#: 2.30 holds SDMX 3.x's geographic codelists (#422): ``code`` gains
#: ``geo_value`` (a `GeoFeatureSetCode`'s feature set) and ``geo_cell`` (a
#: `GeoGridCode`'s cell), NULL on a plain code; the new ``geo_codelist`` holds a
#: geographic codelist's kind and a grid's definition. Its artefact type stays
#: ``Codelist``, which is its URN class.
#:
#: 2.31 holds SDMX 3.x's value lists (#422): artefact type ``ValueList``, and
#: ``value_item`` their values in order. A component a value list enumerates
#: keeps its URN in the ``codelist_urn`` column it always used — the URN's
#: class says which kind it names.
#:
#: 2.32 holds the last of #422's register rows: ``region_selection`` gains
#: ``time_valid_from`` / ``time_valid_to`` (a time range's own validity);
#: ``annotation_url`` holds an annotation's URLs, each with its language, in
#: place of ``annotation.url``; and ``facet_sentinel`` a text format's
#: sentinel values.
#:
#: 2.33 holds a maintainable's external reference and an item scheme's
#: partiality (#422): ``artefact`` gains ``service_url``, ``structure_url`` and
#: ``is_partial``, and its ``is_external_ref`` column, declared and never
#: written, is written at last.
#:
#: 2.34 holds a format's ``interval``, ``time_interval``, ``start_time`` and
#: ``end_time`` in every facet column group (#422), and on ``concept`` its
#: core representation's enumeration format and its ISO concept reference.
#:
#: 2.35 holds #422's smaller drops: ``artefact.is_partial_language``; a
#: ``data_structure`` table for a DSD's component-list uris and its
#: ``evolving_structure``; ``metadata_structure.attribute_list_uri``; and the
#: validity of a ``data_key`` and an ``item_map``. ``dsd_component``'s
#: ``measure_usage``, declared and never written, is written at last.
#:
#: 2.36 holds the last of the attribute census's drops (#422): a contact's
#: ``contact_id``, a metadata attribute usage's ``uri``, a concept's core
#: representation ``min_occurs`` / ``max_occurs``, a structure map's
#: ``is_extension`` and a hierarchy node's ``version``.
#:
#: 2.37 holds a data key's components (#422): ``data_key_component`` and
#: ``data_key_component_value``, the region selection tables' twins.
#:
#: 2.38 holds a structure map's SDMX 3.x date and value maps (#422):
#: ``epoch_map``, ``date_pattern_map``, ``frequency_format_mapping`` and
#: ``fixed_value_map``, and ``component_map.representation_map_urn``.
#:
#: 2.39 holds N:M maps (#422). ``component_map``'s ``source_id`` /
#: ``target_id`` become the lists ``sources`` / ``targets``. A representation
#: map's endpoints move to ``representation_map_source`` / ``_target``, data
#: types included, and ``value_map`` becomes ``representation_mapping`` —
#: ordered, every source value's flags held, so regex and index mappings are
#: no longer dropped on write.
#:
#: 2.40 holds a constraint attachment whole (#422). The dataflow, DSD and
#: provision agreement slots become the ordered ``data_constraint_dataflow`` /
#: ``_dsd`` / ``_provision`` tables, since SDMX attaches to several of a kind;
#: ``data_constraint`` gains ``simple_data_sources``, and the data sets and
#: queryable data sources their own tables. The provider stays a column.
#:
#: 2.41 holds SDMX-ML 2.1's DSD drops (#422): ``dsd_component``'s
#: ``attachment_groups``, and ``dsd_group``'s ``attachment_constraint_urn``. A
#: measure dimension's concept roles use the existing column.
#:
#: 2.42 holds SDMX 3.x's ``com:Link`` (#422): ``link``, every identifiable's
#: links in order, keyed by owner URN as ``annotation`` is.
#:
#: 2.43 holds what SDMX-JSON's links say besides (#422): ``link.hreflang``,
#: and ``annotation_url``'s ``link_urn`` and ``link_type``.
#:
#: 2.44 holds SDMX-ML 2.1's ``<RepresentationMapping>`` (#422): a
#: ``component_map`` gains a ``to_*`` facet group and ``to_value_type`` for its
#: ToTextFormat and ToValueType. A codelist map or value map needs nothing new:
#: both are a ``representation_map_urn``.
#:
#: 2.45 drops what no SDMX format carries (#419): ``hierarchy_node``'s
#: ``name`` and ``description`` — a hierarchical code is identifiable, not
#: nameable, and the name it shows is its code's — and ``agency.parent_id``,
#: which ``AgencyType`` restricts away.
#:
#: 2.46 holds an organisation scheme map (#448): ``organisation_scheme_map``,
#: its ends as ``concept_scheme_map``'s; its pairs are ``item_map`` rows.
#:
#: 2.47 holds a constraint's key sets (#479): ``data_key_set``, each with its
#: ``is_included``, and ``data_key.set_idx`` placing a key in one. Keys stay
#: numbered across the constraint, so their values and components need nothing.
#:
#: 2.48 holds a data consumer scheme and an organisation unit scheme (#469):
#: ``data_consumer`` and ``organisation_unit``, item tables as the provider
#: schemes have, the unit's with a ``parent_id``. Contacts go in
#: ``organisation_contact``, as every organisation's do.
#:
#: 2.49 holds each binding's availability (GH-194): ``availability``, one row
#: per bound dataflow with its observation count, and ``availability_value``,
#: the values each dimension holds. It is the SDMX 3.1 ``AvailabilityConstraint``
#: for the unfiltered query, which the IM does not make maintainable, so it is
#: keyed by the dataflow rather than stored as an artefact.
#:
#: 2.50 drops ``dataflow_binding``'s ``partition_layout`` and ``sort_order``
#: (GH-205): a table's partitioning and sort are DuckLake's, in its catalog.
#:
#: 2.51 holds a reported attribute's place in its set (GH-197):
#: ``metadata_value.ordinal``, the document order the tree is rebuilt in.
#:
#: 2.52 names an unsealed edition's ``_meta`` keys as manifest 3.0 does
#: (GH-250): ``edition_id`` and ``previous``, where 2.51 had
#: ``publication_token`` and ``previous_token``.
SCHEMA_VERSION = "2.52"


#: The `Representation` sum type, as columns — **defined once and interpolated
#: into every table that carries one** (`concept`, `dsd_component`,
#: `metadata_attribute`).
#:
#: Three hand-written copies is what this replaces, and they had already
#: drifted twice: `concept` prefixed every column `repr_` while the other two
#: did not, and `metadata_attribute` was missing `is_multilingual` entirely —
#: so a `MetadataAttribute` carrying a multilingual facet round-tripped as
#: `None` while a `Concept` and a `Dimension` kept it. Neither drift was
#: visible from any one table.
#:
#: `repr_kind` discriminates the two arms of
#: `Representation = Ref[Codelist] | Facet | None`: `"enumeration"` uses
#: `codelist_urn`, `"facet"` uses the rest. Interpolated rather than factored
#: into a side table because the three owners do not share a key shape —
#: `concept` and `metadata_attribute` are keyed by URN, `dsd_component` by
#: `(dsd_urn, component_id)` — so a shared table would need a polymorphic
#: key, which the edge graph cannot derive a cardinality from.
#:
#: `tests/unit/test_storage_schema.py` asserts the three tables carry an
#: identical facet column group, so this stays derived rather than becoming
#: three copies again.
_FACET_COLUMNS = """\
    repr_kind        TEXT,
    codelist_urn     TEXT,
    text_type        TEXT,
    min_length       INT,
    max_length       INT,
    min_value        DOUBLE,
    max_value        DOUBLE,
    decimals         SMALLINT,
    pattern          TEXT,
    is_sequence      BOOLEAN,
    start_value      DOUBLE,
    end_value        DOUBLE,
    is_multilingual  BOOLEAN,
    interval         DOUBLE,
    time_interval    TEXT,
    start_time       TEXT,
    end_time         TEXT,\
"""

#: The facet column *names*, derived from the DDL above so the writers and the
#: guard cannot list a different set than the schema creates.
FACET_COLUMN_NAMES: tuple[str, ...] = tuple(
    line.split()[0] for line in _FACET_COLUMNS.strip().splitlines() if line.strip()
)

#: A component's `enumeration_format` (#438): the facet constraining the codes
#: of an *enumerated* representation, so it sits beside `codelist_urn` rather
#: than in the facet columns, which are the free-text arm of the
#: representation and which the query language and raw SQL read as exactly
#: that. The same fields, prefixed, derived from `_FACET_COLUMNS` so the two
#: groups cannot drift. DSD components, metadata attributes and — since #422 —
#: concepts carry one; a level's representation has no enumerated arm.
_ENUMERATION_FORMAT_COLUMNS = "\n".join(f"    enum_{line.strip()}" for line in _FACET_COLUMNS.strip().splitlines()[2:])

#: A text format's sentinel values (#422), on the three tables whose facet
#: may be one: a list, in order, of ``{value, name, description}``. Apart from
#: `_FACET_COLUMNS`, because neither an enumeration format nor a level's
#: coding format may carry one, and last in each table, which is where an
#: in-place migration's ``ADD COLUMN`` puts it too.
_SENTINEL_COLUMN = (
    "    sentinel_values  STRUCT(value VARCHAR, name MAP(VARCHAR, VARCHAR), description MAP(VARCHAR, VARCHAR))[],"
)

#: Their names, in `_FACET_COLUMNS` order.
ENUMERATION_FORMAT_COLUMN_NAMES: tuple[str, ...] = tuple(f"enum_{name}" for name in FACET_COLUMN_NAMES[2:])

#: A 2.1 component map's ``<ToTextFormat>`` (#422): the `enum_*` group's
#: fields, prefixed ``to_``, on ``component_map``.
_TO_TEXT_FORMAT_COLUMNS = "\n".join(f"    to_{line.strip()}" for line in _FACET_COLUMNS.strip().splitlines()[2:])

#: Their names, in `_FACET_COLUMNS` order.
TO_TEXT_FORMAT_COLUMN_NAMES: tuple[str, ...] = tuple(f"to_{name}" for name in FACET_COLUMN_NAMES[2:])


# ── DDL ─────────────────────────────────────────────────────────────────

_DDL = f"""\
-- Schema creation
CREATE SCHEMA IF NOT EXISTS sdmx;
SET search_path = 'sdmx';

-- Schema version tracking
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── Scratch: which artefacts a remote fetch brought ─────────────────
--
-- A query over a `RestRegistry` must answer from *its own fetch*, not from
-- everything the store has accumulated (design §5.13, #340). Without this a
-- provider serving only CL_LIVE answered ['CL_LIVE', 'CL_RETIRED'], because
-- the store still held a withdrawn artefact from an earlier session.
--
-- `fetch_key` is `canonical_key(plan)` rendered as text — the same key the
-- fetch cache uses, so membership and cache agree by construction. Computable
-- while the SQL is compiled, which is why this exists as a table rather than
-- as a `fetched_at` watermark: terminals compile before they fetch, so a
-- timestamp does not exist yet when the predicate is built.
--
-- **Scratch, not data.** Rows are replaced per key on every fetch and are
-- inert once their key leaves the process's bounded fetch cache. Truncating
-- the table is always safe; it only costs a re-fetch.
CREATE TABLE IF NOT EXISTS fetch_scope (
    fetch_key TEXT NOT NULL,
    urn       TEXT NOT NULL,
    PRIMARY KEY (fetch_key, urn)
);

-- ── Layer 1: Catalogue spine ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS artefact (
    urn             TEXT PRIMARY KEY,
    artefact_type   TEXT NOT NULL,
    agency_id       TEXT NOT NULL,
    resource_id     TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1.0',
    -- Sortable form of `version`, baked at write time (#311). `version` is
    -- text, so ordering it directly puts 1.9 after 1.10 and "the latest
    -- version" returns the wrong artefact. Immutable within an artefact
    -- version, so a bake rather than a runtime cost — the same call
    -- `docs/design/caching.md` makes for `code.depth`.
    -- The default is `sort_key('1.0')`, matching `version`'s own default.
    -- Written out rather than computed because this is DDL; a test asserts
    -- the two agree, so a change to the key format cannot leave a stale
    -- literal that silently mis-sorts every row inserted without one.
    version_sort    TEXT NOT NULL DEFAULT
        '000000001.000000000.000000000.000000000.000000000.000000000.000000000.000000000~',
    -- Baked from `version` (`model.version.is_final`, #420): SDMX 3.0 reads
    -- finality from the version, so this is derived, not stored information.
    -- A column so `q.artefact.is_final` filters without parsing per row.
    is_final        BOOLEAN NOT NULL DEFAULT FALSE,
    -- SDMX 2.1's `isFinal` where it disagrees with the version; NULL when
    -- it agrees or the artefact never came from 2.1 (#424).
    legacy_is_final BOOLEAN,
    -- `isExternalReference` with the URLs that say where the full artefact
    -- is (#422); `is_partial` an item scheme's `isPartial`, false elsewhere.
    is_external_ref BOOLEAN NOT NULL DEFAULT FALSE,
    service_url     TEXT,
    structure_url   TEXT,
    is_partial      BOOLEAN NOT NULL DEFAULT FALSE,
    -- SDMX 3.1's `isPartialLanguage` (#422).
    is_partial_language BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from      TIMESTAMP,
    valid_to        TIMESTAMP,
    source_registry TEXT,
    fetched_at      TIMESTAMP,
    -- SDMX IdentifiableType/@uri: an external link describing the artefact.
    -- Every item and component table carries its own `uri` column too (#442);
    -- until then items lost theirs on a store/load round-trip (#238).
    uri             TEXT
);

CREATE INDEX IF NOT EXISTS idx_artefact_type
    ON artefact(artefact_type);
CREATE INDEX IF NOT EXISTS idx_artefact_agency_id
    ON artefact(agency_id, resource_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artefact_identity
    ON artefact(artefact_type, agency_id, resource_id, version);

-- ── Layer 2: Multilingual text ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS localized_text (
    owner_urn   TEXT NOT NULL,
    field       TEXT NOT NULL,
    lang        TEXT NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (owner_urn, field, lang)
);

CREATE INDEX IF NOT EXISTS idx_lt_owner ON localized_text(owner_urn);

-- ── Layer 3: Annotations ────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS annotation_id_seq;

CREATE TABLE IF NOT EXISTS annotation (
    id              BIGINT PRIMARY KEY DEFAULT nextval('annotation_id_seq'),
    owner_urn       TEXT NOT NULL,
    -- The *maintainable* the owner belongs to, denormalised (#145).
    -- ``owner_urn`` is usually an item URN — a code, a concept, a DSD
    -- component — and an item URN is not a prefix of its artefact's: the
    -- class differs (``Code=`` against ``Codelist=``), so only the
    -- ``agency:id(version)`` tail is shared. Recovering the artefact from
    -- the owner therefore needed ``LIKE '%=SUFFIX.%'``, whose leading
    -- wildcard no index can serve: clearing one artefact's annotations
    -- scanned all 585,675 rows of a real edition, 22.5 ms a time.
    owner_artefact_urn TEXT NOT NULL,
    ordinal         INT NOT NULL,
    annotation_id   TEXT,
    title           TEXT,
    annotation_type TEXT,
    -- SDMX AnnotationValue: the non-localised body (#368). Distinct from
    -- ``annotation_text``, which is the per-language table.
    value           TEXT
);

CREATE INDEX IF NOT EXISTS idx_ann_owner ON annotation(owner_urn);
CREATE INDEX IF NOT EXISTS idx_ann_artefact ON annotation(owner_artefact_urn);
CREATE INDEX IF NOT EXISTS idx_ann_type ON annotation(annotation_type);
CREATE INDEX IF NOT EXISTS idx_ann_type_owner
    ON annotation(annotation_type, owner_urn);

CREATE TABLE IF NOT EXISTS annotation_text (
    annotation_id   BIGINT NOT NULL,
    lang            TEXT NOT NULL,
    text            TEXT NOT NULL,
    PRIMARY KEY (annotation_id, lang)
);

-- An annotation's URLs, in order, each with the language of its resource
-- when localised (#422). SDMX 3.x allows several; 2.1 one. Keyed by position
-- rather than language, since two may share a language or have none.
-- ``link_urn`` and ``link_type`` are SDMX-JSON's, on the link that spells
-- the URL; not ``urn``, which names a row's own identity everywhere else.
CREATE TABLE IF NOT EXISTS annotation_url (
    annotation_id   BIGINT NOT NULL,
    position        INT NOT NULL,
    url             TEXT NOT NULL,
    lang            TEXT,
    link_urn        TEXT,
    link_type       TEXT,
    PRIMARY KEY (annotation_id, position)
);

-- An identifiable's ``<com:Link>`` elements, in order (#422), keyed by its
-- URN as an annotation is, with the maintainable it belongs to beside it for
-- the delete cascade. ``urn`` is an ``xs:anyURI`` pointer, not an edge;
-- ``hreflang`` is SDMX-JSON's alone.
CREATE TABLE IF NOT EXISTS link (
    owner_urn           TEXT NOT NULL,
    owner_artefact_urn  TEXT NOT NULL,
    position            INT NOT NULL,
    rel                 TEXT NOT NULL,
    url                 TEXT NOT NULL,
    urn                 TEXT,
    link_type           TEXT,
    hreflang            TEXT,
    PRIMARY KEY (owner_urn, position)
);

CREATE INDEX IF NOT EXISTS idx_link_artefact ON link(owner_artefact_urn);

-- ── Layer 4: Reference graph ────────────────────────────────────────

-- The version each artefact was published at, when `add_all` ingested it at
-- another because SDMX 3.x has no form for the original (`1.03`, `1.2.3.4`;
-- #420, #424). Provenance, so a rewrite can be reported and audited; deleted
-- with the artefact, so a URN added again without a rewrite loses its row.
CREATE TABLE IF NOT EXISTS version_rewrite (
    urn              TEXT PRIMARY KEY,
    original_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artefact_ref (
    source_urn   TEXT NOT NULL,
    target_urn   TEXT NOT NULL,
    role         TEXT NOT NULL,
    ordinal      INT,
    PRIMARY KEY (source_urn, target_urn, role)
);

CREATE INDEX IF NOT EXISTS idx_ref_target ON artefact_ref(target_urn);
CREATE INDEX IF NOT EXISTS idx_ref_role ON artefact_ref(role);

-- ── Layer 5: Item tables (columnar) ─────────────────────────────────

CREATE TABLE IF NOT EXISTS code (
    code_urn        TEXT PRIMARY KEY,
    codelist_urn    TEXT NOT NULL,
    code_id         TEXT NOT NULL,
    parent_id       TEXT,
    position        INT,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    depth           SMALLINT NOT NULL DEFAULT 0,
    ancestor_ids    TEXT[],
    uri             TEXT,
    -- A geographic code's own field (#422): at most one is set, and which
    -- one names the code's class. Both NULL on a plain `Code`.
    geo_value       TEXT,
    geo_cell        TEXT,
    UNIQUE (codelist_urn, code_id)
);

CREATE INDEX IF NOT EXISTS idx_code_codelist ON code(codelist_urn);
CREATE INDEX IF NOT EXISTS idx_code_parent ON code(codelist_urn, parent_id);

CREATE TABLE IF NOT EXISTS concept (
    concept_urn     TEXT PRIMARY KEY,
    scheme_urn      TEXT NOT NULL,
    concept_id      TEXT NOT NULL,
    parent_id       TEXT,
    position        INT,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    -- Baked at write time like `code` and `category`, so ancestry is array
    -- containment rather than a recursive CTE. `parent_id` predates this and
    -- had been written as a constant NULL since the table existed (#307).
    depth           SMALLINT NOT NULL DEFAULT 0,
    ancestor_ids    TEXT[],
    uri             TEXT,
{_FACET_COLUMNS}
{_SENTINEL_COLUMN}
    -- The core representation's enumeration format, and the concept's ISO
    -- 11179 counterpart (#422): last, where an in-place ADD COLUMN puts them.
{_ENUMERATION_FORMAT_COLUMNS}
    iso_agency            TEXT,
    iso_concept_scheme_id TEXT,
    iso_concept_id        TEXT,
    -- The core representation's occurrence bounds; NULL max is unbounded (#422).
    min_occurs            INT DEFAULT 1,
    max_occurs            INT DEFAULT 1,
    UNIQUE (scheme_urn, concept_id)
);

CREATE INDEX IF NOT EXISTS idx_concept_scheme ON concept(scheme_urn);

CREATE TABLE IF NOT EXISTS category (
    category_urn    TEXT PRIMARY KEY,
    scheme_urn      TEXT NOT NULL,
    category_id     TEXT NOT NULL,
    -- The category's identity in its scheme: its dotted path (#439). The id
    -- repeats across branches; the path does not.
    path            TEXT NOT NULL,
    parent_path     TEXT,
    position        INT,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    depth           SMALLINT NOT NULL DEFAULT 0,
    -- Ancestors root-first, twice: their own ids for `ancestors.id == X`,
    -- their paths for the `ancestors` edge.
    ancestor_ids    TEXT[],
    ancestor_paths  TEXT[],
    uri             TEXT,
    UNIQUE (scheme_urn, path)
);

CREATE INDEX IF NOT EXISTS idx_category_scheme ON category(scheme_urn);

CREATE TABLE IF NOT EXISTS agency (
    agency_urn      TEXT PRIMARY KEY,
    scheme_urn      TEXT NOT NULL,
    agency_id       TEXT NOT NULL,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    uri             TEXT,
    UNIQUE (scheme_urn, agency_id)
);

CREATE INDEX IF NOT EXISTS idx_agency_scheme ON agency(scheme_urn);

CREATE SEQUENCE IF NOT EXISTS organisation_contact_id_seq;

-- Contacts hang off an *organisation*, not an agency. `Agency` and
-- `DataProvider` are both `Organisation` in the IM and carry an identical
-- `contacts` field, so one table serves both — `owner_urn` is whichever it is.
-- It was `agency_contact(agency_urn)`, which is why `DataProvider.contacts`
-- was silently dropped on write for as long as the field has existed (#309):
-- there was nowhere to put it.
CREATE TABLE IF NOT EXISTS organisation_contact (
    id              BIGINT PRIMARY KEY DEFAULT nextval('organisation_contact_id_seq'),
    owner_urn       TEXT NOT NULL,
    ordinal         INT NOT NULL,
    contact_name    MAP(VARCHAR, VARCHAR),
    department      MAP(VARCHAR, VARCHAR),
    role            MAP(VARCHAR, VARCHAR),
    phones          TEXT[],
    faxes           TEXT[],
    x400s           TEXT[],
    uris            TEXT[],
    emails          TEXT[],
    -- A contact's `@id`, a user id for it (#422).
    contact_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_contact_owner ON organisation_contact(owner_urn);

CREATE TABLE IF NOT EXISTS data_provider (
    provider_urn    TEXT PRIMARY KEY,
    scheme_urn      TEXT NOT NULL,
    provider_id     TEXT NOT NULL,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    uri             TEXT,
    UNIQUE (scheme_urn, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_scheme ON data_provider(scheme_urn);

-- The metadata twin of `data_provider` (#315). Separate table rather than a
-- shared one with a kind column, mirroring how the schemes are separate
-- artefacts: `metadata_provision_agreement.metadata_provider_urn` resolves
-- here, and until this existed it resolved nowhere.
CREATE TABLE IF NOT EXISTS metadata_provider (
    provider_urn    TEXT PRIMARY KEY,
    scheme_urn      TEXT NOT NULL,
    provider_id     TEXT NOT NULL,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    uri             TEXT,
    UNIQUE (scheme_urn, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_mdprovider_scheme ON metadata_provider(scheme_urn);

-- A data consumer, the receiving twin of `data_provider` (#469). Separate for
-- the reason `metadata_provider` gives.
CREATE TABLE IF NOT EXISTS data_consumer (
    consumer_urn    TEXT PRIMARY KEY,
    scheme_urn      TEXT NOT NULL,
    consumer_id     TEXT NOT NULL,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    uri             TEXT,
    UNIQUE (scheme_urn, consumer_id)
);

CREATE INDEX IF NOT EXISTS idx_consumer_scheme ON data_consumer(scheme_urn);

-- An organisation unit (#469). The one organisation item with a hierarchy:
-- `parent_id` names another unit of the same scheme, as `code.parent_id` does.
CREATE TABLE IF NOT EXISTS organisation_unit (
    unit_urn        TEXT PRIMARY KEY,
    scheme_urn      TEXT NOT NULL,
    unit_id         TEXT NOT NULL,
    parent_id       TEXT,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    uri             TEXT,
    UNIQUE (scheme_urn, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_unit_scheme ON organisation_unit(scheme_urn);

-- ── Layer 6: Snowflake arms (structure tables) ──────────────────────

CREATE TABLE IF NOT EXISTS dsd_component (
    dsd_urn          TEXT NOT NULL,
    component_id     TEXT NOT NULL,
    component_type   TEXT NOT NULL,
    position         INT,
    concept_urn      TEXT,
{_FACET_COLUMNS}
{_ENUMERATION_FORMAT_COLUMNS}
    usage_status     TEXT,
    attachment_level TEXT,
    attachment_group TEXT,
    related_dimensions VARCHAR[],
    measure_usage    TEXT,
    concept_roles    VARCHAR[],
    uri              TEXT,
    optional_dimensions VARCHAR[],
    related_measures VARCHAR[],
    min_occurs       INT DEFAULT 1,
    max_occurs       INT DEFAULT 1,
{_SENTINEL_COLUMN}
    -- SDMX-ML 2.1's attachment groups, beside `related_dimensions` (#422).
    attachment_groups VARCHAR[],
    PRIMARY KEY (dsd_urn, component_id)
);

-- A DSD's metadata attribute usages (#422). Apart from `dsd_component`: a
-- usage is known by the MSD attribute it uses, whose id may equal a data
-- attribute's.
CREATE TABLE IF NOT EXISTS dsd_metadata_attribute (
    dsd_urn             TEXT NOT NULL,
    attribute_id        TEXT NOT NULL,
    position            INT,
    attachment_level    TEXT,
    attachment_group    TEXT,
    related_dimensions  VARCHAR[],
    optional_dimensions VARCHAR[],
    uri                 TEXT,
    PRIMARY KEY (dsd_urn, attribute_id)
);

CREATE INDEX IF NOT EXISTS idx_dsd_comp_codelist
    ON dsd_component(codelist_urn);

CREATE TABLE IF NOT EXISTS dsd_group (
    dsd_urn     TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    uri         TEXT,
    -- SDMX-ML 2.1's alternative to `group_dimension` rows (#422).
    attachment_constraint_urn TEXT,
    PRIMARY KEY (dsd_urn, group_id)
);

CREATE TABLE IF NOT EXISTS group_dimension (
    dsd_urn      TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    dimension_id TEXT NOT NULL,
    PRIMARY KEY (dsd_urn, group_id, dimension_id)
);

-- ── Layer 7: Type extension tables ──────────────────────────────────

CREATE TABLE IF NOT EXISTS dataflow (
    urn         TEXT PRIMARY KEY,
    dsd_urn     TEXT,
    -- SDMX 3.1 <DimensionConstraint>: the dimensions this flow commits to
    -- using, when its DSD may grow more in a later minor version. A list
    -- column rather than a child table because it is a set of ids with no
    -- attributes of its own, and nothing joins to it — the same shape as
    -- `dsd_component.related_dimensions`. Empty means unconstrained (#323).
    dimension_constraint TEXT[]
);

CREATE TABLE IF NOT EXISTS provision_agreement (
    urn              TEXT PRIMARY KEY,
    dataflow_urn     TEXT,
    data_provider_urn TEXT
);

CREATE TABLE IF NOT EXISTS metadata_provision_agreement (
    urn                   TEXT PRIMARY KEY,
    metadataflow_urn      TEXT,
    metadata_provider_urn TEXT
);

CREATE TABLE IF NOT EXISTS categorisation (
    urn             TEXT PRIMARY KEY,
    source_urn      TEXT,
    target_urn      TEXT
);

CREATE TABLE IF NOT EXISTS hierarchy_association (
    urn                  TEXT PRIMARY KEY,
    linked_hierarchy_urn TEXT,
    linked_object_urn    TEXT,
    context_object_urn   TEXT,
    -- `linked_object_urn` split into the halves a join can use: the
    -- maintainable that owns the linked item, and the item's own id. An HA
    -- linking `REF_AREA` names a Dimension URN, which no table keys on —
    -- `dsd_component` keys on (dsd_urn, component_id). Baked at write time
    -- rather than parsed per query, and NULL when the linked object is a
    -- maintainable in its own right, or an item class no scheme in the model
    -- holds -- see `writers._split_item_urn`, which reads the owner off the
    -- item class rather than off a table of its own.
    linked_object_owner_urn TEXT,
    linked_object_item_id   TEXT
);

CREATE INDEX IF NOT EXISTS idx_ha_linked_item
    ON hierarchy_association(linked_object_owner_urn, linked_object_item_id);

-- Which hierarchy applies to which component, per dataflow, ranked (#372).
--
-- The resolution rule, stated once. `HierarchyAssociation` binds a hierarchy
-- to a component; an association naming this dataflow as its context is
-- *scoped* and outranks an *unscoped* one, which is the default for any flow
-- using that component. `rank = 1` is the winner and every applicable
-- hierarchy is present, so ambiguity is visible rather than resolved away.
--
-- A view rather than a table: it is derived from three relations that are
-- already stored, and `docs/design/caching.md` puts layout ahead of a cache
-- of something cheap to compute.
--
-- `agency_id` comes off the dataflow's spine row so the relation bounds the
-- way a maintainable root does — see `SDMX_REST.bounding`.
CREATE OR REPLACE VIEW hierarchy_applicability AS
SELECT
    df.urn AS dataflow_urn,
    a.agency_id AS agency_id,
    c.dsd_urn AS dsd_urn,
    c.component_id AS component_id,
    ha.urn AS association_urn,
    ha.linked_hierarchy_urn AS hierarchy_urn,
    (ha.context_object_urn IS NOT NULL) AS scoped,
    row_number() OVER (
        PARTITION BY df.urn, c.dsd_urn, c.component_id
        ORDER BY (ha.context_object_urn IS NOT NULL) DESC, ha.urn
    ) AS rank
FROM dataflow df
JOIN artefact a ON a.urn = df.urn
JOIN dsd_component c ON c.dsd_urn = df.dsd_urn
JOIN hierarchy_association ha
    ON ha.linked_object_owner_urn = c.dsd_urn
   AND ha.linked_object_item_id = c.component_id
   AND (ha.context_object_urn IS NULL OR ha.context_object_urn = df.urn)
WHERE ha.linked_hierarchy_urn IS NOT NULL;

CREATE TABLE IF NOT EXISTS hierarchy_meta (
    urn               TEXT PRIMARY KEY,
    has_formal_levels BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS hierarchy_level (
    hierarchy_urn   TEXT NOT NULL,
    level_id        TEXT NOT NULL,
    level_idx       SMALLINT NOT NULL,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    -- `Level.coding_format` is a plain Facet, so `repr_kind` is always
    -- 'facet' or NULL here and `codelist_urn` is never used. The whole group
    -- is interpolated anyway: a fourth hand-written subset is how the other
    -- three drifted, and the writer expands it from one place too (#160).
{_FACET_COLUMNS}
    uri             TEXT,
    PRIMARY KEY (hierarchy_urn, level_id)
);

CREATE TABLE IF NOT EXISTS hierarchy_node (
    node_urn        TEXT PRIMARY KEY,
    hierarchy_urn   TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    -- See `category.path` (#439).
    path            TEXT NOT NULL,
    parent_path     TEXT,
    position        INT,
    code_urn        TEXT,
    level_id        TEXT,
    valid_from      TIMESTAMP,
    valid_to        TIMESTAMP,
    depth           SMALLINT NOT NULL DEFAULT 0,
    ancestor_ids    TEXT[],
    ancestor_paths  TEXT[],
    uri             TEXT,
    -- 2.1's informational `@version` on a hierarchical code (#422).
    version         TEXT,
    UNIQUE (hierarchy_urn, path)
);

CREATE INDEX IF NOT EXISTS idx_hnode_hierarchy
    ON hierarchy_node(hierarchy_urn);
CREATE INDEX IF NOT EXISTS idx_hnode_parent
    ON hierarchy_node(hierarchy_urn, parent_path);

-- ── Layer 7b: Metadata Structure (MSD) tables ───────────────────────

-- What a DataStructure holds of itself beyond its components (#422): each
-- component list's `@uri` (their annotations are owned by the list's URN in
-- `annotation`), and SDMX 3.1's `evolvingStructure`.
CREATE TABLE IF NOT EXISTS data_structure (
    urn                      TEXT PRIMARY KEY,
    dimension_descriptor_uri TEXT,
    attribute_descriptor_uri TEXT,
    measure_descriptor_uri   TEXT,
    evolving_structure       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS metadata_structure (
    urn TEXT PRIMARY KEY,
    -- The attribute list's `@uri` (#422); its annotations are owned by the
    -- list's own URN in `annotation`.
    attribute_list_uri TEXT
);

CREATE TABLE IF NOT EXISTS metadata_attribute (
    attribute_urn        TEXT PRIMARY KEY,
    msd_urn              TEXT NOT NULL,
    attribute_id         TEXT NOT NULL,
    -- See `category.path` (#439).
    path                 TEXT NOT NULL,
    parent_path          TEXT,
    position             INT,
    concept_urn          TEXT,
{_FACET_COLUMNS}
{_ENUMERATION_FORMAT_COLUMNS}
    min_occurs           INT NOT NULL DEFAULT 0,
    max_occurs           INT,
    is_presentational    BOOLEAN NOT NULL DEFAULT FALSE,
    depth                SMALLINT NOT NULL DEFAULT 0,
    ancestor_ids         TEXT[],
    ancestor_paths       TEXT[],
    uri                  TEXT,
{_SENTINEL_COLUMN}
    UNIQUE (msd_urn, path)
);

CREATE INDEX IF NOT EXISTS idx_md_attr_msd ON metadata_attribute(msd_urn);
CREATE INDEX IF NOT EXISTS idx_md_attr_parent
    ON metadata_attribute(msd_urn, parent_path);

CREATE TABLE IF NOT EXISTS metadataflow (
    urn      TEXT PRIMARY KEY,
    msd_urn  TEXT
);

CREATE TABLE IF NOT EXISTS metadata_set (
    urn            TEXT PRIMARY KEY,
    structure_urn  TEXT NOT NULL,
    structure_kind TEXT NOT NULL DEFAULT 'MetadataStructure'
);

CREATE TABLE IF NOT EXISTS metadata_target (
    set_urn     TEXT NOT NULL,
    ordinal     INT NOT NULL,
    target_urn  TEXT NOT NULL,
    PRIMARY KEY (set_urn, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_md_target_set ON metadata_target(set_urn);
CREATE INDEX IF NOT EXISTS idx_md_target_urn ON metadata_target(target_urn);

-- Declared (structural) targets of a metadata product — the artefacts a
-- Metadataflow or MetadataProvisionAgreement says its reference metadata
-- *may* describe (SDMX 3.0 moved the Metadata Target off the MSD onto the
-- flow/provision). Distinct from ``metadata_target`` above, which records
-- the *populated* targets of a reported MetadataSet. ``target_urn`` may be a
-- wildcarded ``WildcardUrnType`` (e.g. ``Dataflow=ESTAT:CPI(*)``), so this
-- is deliberately NOT part of the resolved ``artefact_ref`` graph.
CREATE TABLE IF NOT EXISTS metadata_declared_target (
    source_urn  TEXT NOT NULL,
    ordinal     INT NOT NULL,
    target_urn  TEXT NOT NULL,
    PRIMARY KEY (source_urn, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_md_decl_target_source ON metadata_declared_target(source_urn);

-- ── Layer 7c: DataflowBinding (Parquet/DuckLake binding artefact) ───
--
-- One row per binding; the binding's URN mirrors the bound dataflow's
-- identity (one binding per dataflow per version, v1).

CREATE TABLE IF NOT EXISTS dataflow_binding (
    urn                          TEXT PRIMARY KEY,
    dataflow_urn                 TEXT NOT NULL,
    table_location               TEXT NOT NULL,
    database_role                TEXT NOT NULL DEFAULT 'lake',
    dimension_column_map         MAP(VARCHAR, VARCHAR),
    measure_column_map           MAP(VARCHAR, VARCHAR),
    attribute_column_map         MAP(VARCHAR, VARCHAR),
    structural_version           TEXT NOT NULL,
    binding_source_registry      TEXT NOT NULL,
    -- TIMESTAMPs are stored naive UTC: DuckDB's TIMESTAMPTZ python path
    -- requires pytz, but the model contract is tz-aware. The writer
    -- normalises to UTC + strips tzinfo on write; the reader re-tags
    -- with UTC on read. Round-trip is exact for UTC inputs, lossless
    -- (instant-preserving) for any tz-aware input.
    materialized_at              TIMESTAMP NOT NULL,
    bound_at                     TIMESTAMP NOT NULL,
    series_attributes_location   TEXT
);

CREATE INDEX IF NOT EXISTS idx_binding_dataflow
    ON dataflow_binding(dataflow_urn);

-- What data a bound dataflow holds: the answer to its unfiltered availability
-- query, an SDMX 3.1 AvailabilityConstraint, written at publish (GH-194). Not
-- an artefact: the IM gives it no id, version or name, so it is keyed by the
-- dataflow it describes.
CREATE TABLE IF NOT EXISTS availability (
    dataflow_urn  TEXT PRIMARY KEY,
    obs_count     BIGINT NOT NULL,
    -- NULL: the series count is not computed at publish.
    series_count  BIGINT
);

-- One row per value a dimension holds in the data: the constraint's one
-- cube region, a KeyValue per dimension.
CREATE TABLE IF NOT EXISTS availability_value (
    dataflow_urn  TEXT NOT NULL,
    dimension_id  TEXT NOT NULL,
    value         TEXT NOT NULL,
    PRIMARY KEY (dataflow_urn, dimension_id, value)
);

CREATE TABLE IF NOT EXISTS metadata_value (
    -- One row per (set, attribute_path, sibling_index).
    -- attribute_path is the dotted hierarchical attribute id (e.g.
    -- "QUALITY.COVERAGE"); sibling_index disambiguates repeated
    -- ReportedAttributes with the same id (max_occurs > 1 in the MSD).
    -- ordinal is the row's position in the set, in document order (a
    -- pre-order walk), which the tree is rebuilt in.
    -- value_kind is 'scalar' | 'i18n' (XHTML is i18n with HTML markup,
    -- distinguished only by the MSD's data type at the application layer).
    set_urn         TEXT NOT NULL,
    attribute_path  TEXT NOT NULL,
    sibling_index   INT NOT NULL,
    ordinal         INT NOT NULL,
    value_kind      TEXT,
    scalar          TEXT,
    i18n            MAP(VARCHAR, VARCHAR),
    PRIMARY KEY (set_urn, attribute_path, sibling_index)
);

CREATE INDEX IF NOT EXISTS idx_md_value_set ON metadata_value(set_urn);

-- ── Layer 8: Constraint tables ──────────────────────────────────────

-- SDMX 3.0 codelist composition: a Codelist may extend others, taking all
-- their codes or a selection, optionally prefixed. Two tables because a
-- selection holds an ordered list of member values, each with its own cascade
-- rule — a list column could hold the values but not the cascade beside them.
--
-- `selection_mode` NULL means the extension takes every code of the extended
-- codelist; non-NULL means the `code_selection_member` rows apply (#160).
-- A value list's values, in order (#422). A value item is not identifiable —
-- no URN, and an id that is any string — so the key is the list and the
-- position; its annotations hang off ``<list urn>.<id>``, as an item's do.
CREATE TABLE IF NOT EXISTS value_item (
    value_list_urn  TEXT NOT NULL,
    position        INT NOT NULL,
    item_id         TEXT NOT NULL,
    name            MAP(VARCHAR, VARCHAR),
    description     MAP(VARCHAR, VARCHAR),
    PRIMARY KEY (value_list_urn, position),
    UNIQUE (value_list_urn, item_id)
);

-- A geographic codelist's kind — `GeographicCodelist` or `GeoGridCodelist` —
-- and a grid's definition (#422). No row: a plain `Codelist`. Not derivable
-- from its codes, since a plain codelist may hold geographic codes too.
CREATE TABLE IF NOT EXISTS geo_codelist (
    codelist_urn    TEXT PRIMARY KEY,
    geo_type        TEXT NOT NULL,
    grid_definition TEXT
);

CREATE TABLE IF NOT EXISTS codelist_extension (
    codelist_urn    TEXT NOT NULL,
    position        SMALLINT NOT NULL,
    extends_urn     TEXT NOT NULL,
    prefix          TEXT,
    selection_mode  TEXT,
    PRIMARY KEY (codelist_urn, position)
);

CREATE TABLE IF NOT EXISTS code_selection_member (
    codelist_urn    TEXT NOT NULL,
    position        SMALLINT NOT NULL,
    member_idx      SMALLINT NOT NULL,
    value           TEXT NOT NULL,
    cascade         TEXT,
    PRIMARY KEY (codelist_urn, position, member_idx)
);

CREATE TABLE IF NOT EXISTS data_constraint (
    urn              TEXT PRIMARY KEY,
    constraint_type  TEXT NOT NULL DEFAULT 'Allowed',
    -- ReferencePeriod and ReleaseCalendar: five scalars between them, each
    -- at most once per constraint, so columns rather than side tables. The
    -- two TIMESTAMPs follow the naive-UTC convention (`sdmxlib/_time.py`).
    -- NULL start and end together mean no reference period was reported;
    -- likewise the three calendar columns (#160).
    ref_period_start TIMESTAMP,
    ref_period_end   TIMESTAMP,
    release_periodicity TEXT,
    release_offset      TEXT,
    release_tolerance   TEXT,
    -- The provider an attachment may name, carried as a column rather than
    -- only as an `artefact_ref` row. `provision_agreement` already does this:
    -- the ref rows stay, for the generic inbound-reference index, and the
    -- column is what a query joins on.
    --
    -- Without it the attachment is a *role-filtered junction* — one hop
    -- through `artefact_ref` with a predicate on `role` — which the edge graph
    -- cannot express, and whose to-one-ness is not derivable either, since
    -- `(source_urn, target_urn, role)` permits several rows per role. The
    -- structures, flows and agreements are several (#422), so each kind has a
    -- table of its own below, keyed by position: one table per kind is what
    -- keeps each an ordinary edge.
    attachment_data_provider_urn  TEXT,
    -- URLs of SDMX-ML data messages; empty when the attachment names none.
    simple_data_sources           TEXT[]
);

CREATE TABLE IF NOT EXISTS data_constraint_dataflow (
    constraint_urn  TEXT NOT NULL,
    ordinal         INT NOT NULL,
    dataflow_urn    TEXT NOT NULL,
    PRIMARY KEY (constraint_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS data_constraint_dsd (
    constraint_urn  TEXT NOT NULL,
    ordinal         INT NOT NULL,
    dsd_urn         TEXT NOT NULL,
    PRIMARY KEY (constraint_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS data_constraint_provision (
    constraint_urn    TEXT NOT NULL,
    ordinal           INT NOT NULL,
    provision_urn     TEXT NOT NULL,
    PRIMARY KEY (constraint_urn, ordinal)
);

-- SDMX-ML 2.1's data sets: a provider and the set's id.
CREATE TABLE IF NOT EXISTS data_constraint_data_set (
    constraint_urn     TEXT NOT NULL,
    ordinal            INT NOT NULL,
    data_provider_urn  TEXT NOT NULL,
    data_set_id        TEXT NOT NULL,
    PRIMARY KEY (constraint_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS data_constraint_queryable_source (
    constraint_urn             TEXT NOT NULL,
    ordinal                    INT NOT NULL,
    data_url                   TEXT NOT NULL,
    is_rest_datasource         BOOLEAN NOT NULL,
    is_web_service_datasource  BOOLEAN NOT NULL,
    wsdl_url                   TEXT,
    wadl_url                   TEXT,
    PRIMARY KEY (constraint_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS cube_region (
    constraint_urn  TEXT NOT NULL,
    region_idx      INT NOT NULL,
    is_included     BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (constraint_urn, region_idx)
);

CREATE TABLE IF NOT EXISTS region_key_value (
    constraint_urn  TEXT NOT NULL,
    region_idx      INT NOT NULL,
    component_id    TEXT NOT NULL,
    value           TEXT NOT NULL,
    is_included     BOOLEAN NOT NULL DEFAULT TRUE,
    cascade         TEXT,
    lang            TEXT,
    valid_from      TEXT,
    valid_to        TEXT,
    PRIMARY KEY (constraint_urn, region_idx, component_id, value)
);

CREATE INDEX IF NOT EXISTS idx_rkv_component
    ON region_key_value(component_id);

-- A constraint's key sets (`DataConstraint.data_key_sets`, `<str:DataKeySet>`),
-- each including or excluding its keys (#479).
CREATE TABLE IF NOT EXISTS data_key_set (
    constraint_urn  TEXT NOT NULL,
    set_idx         INT NOT NULL,
    is_included     BOOLEAN NOT NULL,
    PRIMARY KEY (constraint_urn, set_idx)
);

-- A constraint's explicit observation keys, lost by the store until #437: one
-- row per key, so a key survives with no values, and one per value in its
-- dimension order. `key_idx` counts across the constraint, so the value and
-- component tables key a key alone; `set_idx` places it in its set.
CREATE TABLE IF NOT EXISTS data_key (
    constraint_urn  TEXT NOT NULL,
    key_idx         INT NOT NULL,
    set_idx         INT NOT NULL,
    -- SDMX 3.x's key validity, standard time periods as written (#422).
    valid_from      TEXT,
    valid_to        TEXT,
    PRIMARY KEY (constraint_urn, key_idx)
);

CREATE TABLE IF NOT EXISTS data_key_value (
    constraint_urn  TEXT NOT NULL,
    key_idx         INT NOT NULL,
    position        INT NOT NULL,
    component_id    TEXT NOT NULL,
    value           TEXT NOT NULL,
    PRIMARY KEY (constraint_urn, key_idx, position)
);

-- One row per selection in a region: each dimension `KeyValue`, then each
-- `ComponentValueSet`, in order (#433). The values stay in `region_key_value`,
-- which the query language joins; this holds what a value row cannot — that
-- a selection exists with no values, which kind it is, its position, and its
-- time range (`TimeRange`: the bounds as written, never parsed).
-- A data key's components (#422): the same value sets as a cube region's
-- (`DataComponentValueSetType`), so the same two tables keyed by the key.
-- `is_key` is always false here; it keeps the columns `region_selection`'s, so
-- one writer and one reader serve both.
CREATE TABLE IF NOT EXISTS data_key_component (
    constraint_urn   TEXT NOT NULL,
    key_idx          INT NOT NULL,
    position         INT NOT NULL,
    component_id     TEXT NOT NULL,
    is_key           BOOLEAN NOT NULL,
    start_period     TEXT,
    start_inclusive  BOOLEAN,
    end_period       TEXT,
    end_inclusive    BOOLEAN,
    is_included      BOOLEAN NOT NULL DEFAULT TRUE,
    remove_prefix    BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from       TEXT,
    valid_to         TEXT,
    time_valid_from  TEXT,
    time_valid_to    TEXT,
    PRIMARY KEY (constraint_urn, key_idx, position)
);

CREATE TABLE IF NOT EXISTS data_key_component_value (
    constraint_urn  TEXT NOT NULL,
    key_idx         INT NOT NULL,
    component_id    TEXT NOT NULL,
    value           TEXT NOT NULL,
    is_included     BOOLEAN NOT NULL DEFAULT TRUE,
    cascade         TEXT,
    lang            TEXT,
    valid_from      TEXT,
    valid_to        TEXT,
    PRIMARY KEY (constraint_urn, key_idx, component_id, value)
);

CREATE TABLE IF NOT EXISTS region_selection (
    constraint_urn   TEXT NOT NULL,
    region_idx       INT NOT NULL,
    position         INT NOT NULL,
    component_id     TEXT NOT NULL,
    is_key           BOOLEAN NOT NULL,
    start_period     TEXT,
    start_inclusive  BOOLEAN,
    end_period       TEXT,
    end_inclusive    BOOLEAN,
    is_included      BOOLEAN NOT NULL DEFAULT TRUE,
    remove_prefix    BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from       TEXT,
    valid_to         TEXT,
    -- The time range's own validity, apart from the value set's (#422).
    time_valid_from  TEXT,
    time_valid_to    TEXT,
    PRIMARY KEY (constraint_urn, region_idx, position)
);

-- ── Layer 9: Mapping tables ─────────────────────────────────────────
-- SDMX 3.0 dropped the StructureSet wrapper; each map kind is now a
-- top-level maintainable, and all four are persisted (#395).
--
-- One parent table per kind rather than a shared one with a discriminator,
-- for the reason `metadata_provider` gives: they are separate artefacts, and
-- an edge that resolves to "a map" resolves to nothing in particular.
--
-- The *children* go the other way. The concept, category and organisation
-- scheme maps' `maps` are all `tuple[ItemMap, ...]` — the same IM class — so
-- one `item_map` table serves them, the way `organisation_contact`
-- serves `Agency` and `DataProvider`. `StructureMap.maps` is `ComponentMap`,
-- a different class mapping components rather than items, so it gets its own.
--
-- Every child table carries `ordinal`: the children are a tuple and their
-- order is part of the artefact.
--
-- A RepresentationMap is N:M (#422): several endpoints a side, each a
-- codelist (or value list) or a data type, and mappings holding one value per
-- endpoint. The endpoints get a table a side, so a codelist is an edge the
-- query language can follow; a mapping's values are lists, a source value's
-- three flags parallel to its `sources`.

CREATE TABLE IF NOT EXISTS representation_map (
    urn                 TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS representation_map_source (
    representation_map_urn TEXT NOT NULL,
    ordinal                INT NOT NULL,
    -- One of the two is set.
    codelist_urn           TEXT,
    data_type              TEXT,
    PRIMARY KEY (representation_map_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS representation_map_target (
    representation_map_urn TEXT NOT NULL,
    ordinal                INT NOT NULL,
    codelist_urn           TEXT,
    data_type              TEXT,
    PRIMARY KEY (representation_map_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS representation_mapping (
    representation_map_urn TEXT NOT NULL,
    ordinal                INT NOT NULL,
    sources                TEXT[] NOT NULL,
    source_is_regex        BOOLEAN[] NOT NULL,
    source_start_index     INT[] NOT NULL,
    source_end_index       INT[] NOT NULL,
    targets                TEXT[] NOT NULL,
    -- `xs:date` on the wire and `date` in the model, so DATE rather than the
    -- TIMESTAMP every other validity pair uses (#442).
    valid_from             DATE,
    valid_to               DATE,
    PRIMARY KEY (representation_map_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS concept_scheme_map (
    urn         TEXT PRIMARY KEY,
    source_urn  TEXT,
    target_urn  TEXT
);

CREATE TABLE IF NOT EXISTS category_scheme_map (
    urn         TEXT PRIMARY KEY,
    source_urn  TEXT,
    target_urn  TEXT
);

-- Its ends are any organisation scheme: agencies, data or metadata
-- providers, or a data consumer or organisation unit scheme, unmodelled (#448).
CREATE TABLE IF NOT EXISTS organisation_scheme_map (
    urn         TEXT PRIMARY KEY,
    source_urn  TEXT,
    target_urn  TEXT
);

CREATE TABLE IF NOT EXISTS structure_map (
    urn         TEXT PRIMARY KEY,
    source_urn  TEXT,
    target_urn  TEXT,
    -- 2.1's `@isExtension` (#422).
    is_extension BOOLEAN NOT NULL DEFAULT FALSE
);

-- A structure map's SDMX 3.x date and value maps (#422), one table per kind
-- as the maps themselves are. A date map's (source, target) component pairs
-- are two parallel lists; its annotations, like a frequency format mapping's,
-- are owned by its item URN, and a fixed value map's by `part_owner`.
CREATE TABLE IF NOT EXISTS epoch_map (
    structure_map_urn   TEXT NOT NULL,
    ordinal             INT NOT NULL,
    map_id              TEXT NOT NULL,
    uri                 TEXT,
    source_ids          TEXT[],
    target_ids          TEXT[],
    base_period         TEXT NOT NULL,
    epoch_period        TEXT NOT NULL,
    frequency_dimension TEXT,
    mapped_frequencies  TEXT[],
    target_frequency_id TEXT,
    resolve_period      TEXT,
    PRIMARY KEY (structure_map_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS date_pattern_map (
    structure_map_urn   TEXT NOT NULL,
    ordinal             INT NOT NULL,
    map_id              TEXT NOT NULL,
    uri                 TEXT,
    source_ids          TEXT[],
    target_ids          TEXT[],
    source_pattern      TEXT NOT NULL,
    locale              TEXT NOT NULL,
    frequency_dimension TEXT,
    mapped_frequencies  TEXT[],
    target_frequency_id TEXT,
    resolve_period      TEXT,
    PRIMARY KEY (structure_map_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS frequency_format_mapping (
    structure_map_urn TEXT NOT NULL,
    ordinal           INT NOT NULL,
    mapping_id        TEXT NOT NULL,
    uri               TEXT,
    frequency_id      TEXT NOT NULL,
    date_pattern      TEXT NOT NULL,
    PRIMARY KEY (structure_map_urn, ordinal)
);

CREATE TABLE IF NOT EXISTS fixed_value_map (
    structure_map_urn TEXT NOT NULL,
    ordinal           INT NOT NULL,
    source_id         TEXT,
    target_id         TEXT,
    "values"          TEXT[],
    PRIMARY KEY (structure_map_urn, ordinal)
);

-- Serves both scheme-map kinds; `map_urn` is whichever it is.
CREATE TABLE IF NOT EXISTS item_map (
    map_urn     TEXT NOT NULL,
    ordinal     INT NOT NULL,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    -- SDMX 3.x's `xs:date` validity (#422).
    valid_from  DATE,
    valid_to    DATE,
    PRIMARY KEY (map_urn, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_item_map_source ON item_map(source_id);

CREATE TABLE IF NOT EXISTS component_map (
    structure_map_urn TEXT NOT NULL,
    ordinal           INT NOT NULL,
    -- N:M in SDMX 3.x (#422).
    sources           TEXT[] NOT NULL,
    targets           TEXT[] NOT NULL,
    -- What maps the values (#422): a representation, epoch or date-pattern
    -- map's URN.
    representation_map_urn TEXT,
    -- SDMX-ML 2.1's ToTextFormat and ToValueType (#422), NULL elsewhere.
{_TO_TEXT_FORMAT_COLUMNS}
    to_value_type     TEXT,
    PRIMARY KEY (structure_map_urn, ordinal)
);
"""


# ── All table and view names (for introspection / testing) ──────────

#: Derived relations, kept apart from `ALL_TABLES` rather than folded in.
#: A view is not a table in any way that matters here: it declares no
#: constraints, is not written to, and its rows appear and vanish with the
#: tables underneath it. Callers that mean "the stored relations" would be
#: wrong to include it, so the split is the answer rather than one set and a
#: convention.
ALL_VIEWS: frozenset[str] = frozenset({"hierarchy_applicability"})

ALL_TABLES: frozenset[str] = frozenset(
    {
        "_meta",
        "artefact",
        "fetch_scope",
        "localized_text",
        "annotation",
        "annotation_text",
        "annotation_url",
        "link",
        "version_rewrite",
        "artefact_ref",
        "code",
        "codelist_extension",
        "geo_codelist",
        "value_item",
        "code_selection_member",
        "concept",
        "category",
        "agency",
        "organisation_contact",
        "data_provider",
        "metadata_provider",
        "data_consumer",
        "organisation_unit",
        "dsd_component",
        "dsd_metadata_attribute",
        "dsd_group",
        "group_dimension",
        "dataflow",
        "provision_agreement",
        "metadata_provision_agreement",
        "categorisation",
        "hierarchy_association",
        "hierarchy_meta",
        "hierarchy_level",
        "hierarchy_node",
        "data_constraint",
        "data_constraint_dataflow",
        "data_constraint_dsd",
        "data_constraint_provision",
        "data_constraint_data_set",
        "data_constraint_queryable_source",
        "cube_region",
        "region_key_value",
        "region_selection",
        "data_key_set",
        "data_key",
        "data_key_value",
        "representation_map",
        "representation_map_source",
        "representation_map_target",
        "representation_mapping",
        "concept_scheme_map",
        "category_scheme_map",
        "organisation_scheme_map",
        "structure_map",
        "item_map",
        "component_map",
        "data_structure",
        "epoch_map",
        "date_pattern_map",
        "frequency_format_mapping",
        "fixed_value_map",
        "data_key_component",
        "data_key_component_value",
        "metadata_structure",
        "metadata_attribute",
        "metadataflow",
        "metadata_set",
        "metadata_target",
        "metadata_declared_target",
        "metadata_value",
        "dataflow_binding",
        "availability",
        "availability_value",
    }
)


# ── Public API ──────────────────────────────────────────────────────


def create_schema(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Create the ``sdmx`` schema and all tables.

    Returns a **cursor** with ``search_path = 'sdmx'`` set.  All
    subsequent SQL on this cursor resolves to ``sdmx.*`` without
    explicit schema qualification.

    Idempotent — safe to call on an already-initialised database.
    """
    cursor = conn.cursor()
    cursor.execute("CREATE SCHEMA IF NOT EXISTS sdmx")
    cursor.execute("SET search_path = 'sdmx'")

    # Execute DDL — strip SQL comments before splitting on semicolons
    cleaned = "\n".join(line for line in _DDL.splitlines() if not line.lstrip().startswith("--"))
    for stmt in cleaned.split(";"):
        stripped = stmt.strip()
        if stripped:
            cursor.execute(stripped)

    # Upsert schema version
    cursor.execute(
        """
        INSERT OR REPLACE INTO _meta (key, value)
        VALUES ('schema_version', $ver)
    """,
        {"ver": SCHEMA_VERSION},
    )

    return cursor


def schema_exists(conn: duckdb.DuckDBPyConnection) -> bool:
    """Check whether the ``sdmx`` schema has been created."""
    try:
        conn.execute("SELECT 1 FROM sdmx._meta LIMIT 1")
    except duckdb.CatalogException:
        return False
    else:
        return True


def get_schema_version(conn: duckdb.DuckDBPyConnection) -> str | None:
    """Read the schema version from ``sdmx._meta``.

    Returns ``None`` if the schema doesn't exist or the version key
    is missing.
    """
    return meta_value(conn, "schema_version")


def meta_value(conn: duckdb.DuckDBPyConnection, key: str) -> str | None:
    """The value of `key` in ``sdmx._meta``, or None if the key or the schema is missing.

    Keys: ``schema_version``; ``edition_id`` and ``previous``,
    which `LocalRegistry.build` writes into an edition's store.
    """
    try:
        row = first(conn.execute("SELECT value FROM sdmx._meta WHERE key = ?", [key]))
    except duckdb.CatalogException:
        return None
    return row[0] if row else None


#: The sentinel scan the integrity check runs, and why this shape.
#:
#: ``artefact_type`` is ``NOT NULL``, so ``>= min(artefact_type)`` is a
#: tautology: on an intact store the filtered count *must* equal the total. A
#: shortfall means the scan pruned rows that are there — the filter was
#: answered from segment statistics that no longer describe the data.
#:
#: Self-calibrating, with no value written into it, so it cannot go stale as
#: artefact kinds are added. Run against `artefact` because that table is the
#: spine — one row per maintainable, small by construction — which is what
#: makes this ~1 ms rather than the 30 ms the same probe costs on `code`.
_INTEGRITY_PROBE = "SELECT count(*) FROM artefact WHERE artefact_type >= (SELECT min(artefact_type) FROM artefact)"


class ScanIntegrityError(Exception):
    """A filtered scan of this store returns fewer rows than the store holds.

    Not a schema problem and not corruption of the rows themselves: the data is
    readable, the *metadata the scan prunes with* is wrong. Every DuckDB from
    1.5.0 to 1.6.0-dev honours it identically, so it is a property of the file
    (#400).

    It matters because it is otherwise **invisible**. A store in this state
    answers `SELECT … WHERE col = ?` with a subset and reports success, so a
    read returns an artefact missing children and a re-persist bakes the loss
    in. The edition that prompted this returned 1 of 19 codelists and 0 of 349
    concepts for months.

    The probe is a sentinel, not a proof: it scans the spine, so a store
    damaged only in a child table still passes. It costs one query and would
    have caught the case that motivated it on the day the edition was built,
    which is the trade being made.
    """


def check_scan_integrity(conn: duckdb.DuckDBPyConnection) -> None:
    """Raise `ScanIntegrityError` if a filtered scan of `artefact` loses rows.

    Raises:
        ScanIntegrityError: if the sentinel scan disagrees with the row count.
    """
    total_row = first(conn.execute("SELECT count(*) FROM artefact"))
    total = int(total_row[0]) if total_row else 0
    if not total:
        return
    probed_row = first(conn.execute(_INTEGRITY_PROBE))
    probed = int(probed_row[0]) if probed_row else 0
    if probed == total:
        return
    msg = (
        f"this store holds {total:,} artefacts but a filtered scan finds only "
        f"{probed:,} of them, so reads from it silently lose rows — the row "
        f"data is intact, the statistics the scan prunes with are not (#400). "
        f"Rebuild it: read every artefact out with "
        f"`SET disabled_optimizers='filter_pushdown'` and write them to a fresh "
        f"store. Reading without that setting bakes the loss in."
    )
    raise ScanIntegrityError(msg)


class StoreSchemaError(Exception):
    """A store holds artefacts on a schema version this build does not read.

    Migration is *drop and rebuild*, which is only right for a cache that can
    be fetched again. An edition's ``metadata.duckdb`` is the published record, and a
    store filled with ``add`` may have no source to fetch from, so a populated
    store on another version is refused rather than emptied: sdmxlib 0.68 to
    0.70 opened a 0.67 edition read-write and deleted every artefact in it,
    silently, for the reader and for the older sdmxlib that built it.
    ``LocalRegistry(path, rebuild=True)`` asks for the rebuild.
    """


class ReadOnlySchemaError(StoreSchemaError):
    """A read-only store is not on this build's schema version.

    Raised when a rebuild would otherwise run, but the connection is read-only:
    an empty store to create, or ``rebuild=True``. Raised in place of DuckDB's
    own ``Cannot execute statement of type "DROP" on database … read-only``,
    which names neither version and reads as a bug in the caller.

    First hit when 2.19 landed (#145): the bundle-assertion tier opens
    `metadata.duckdb` read-only by design, so 20 of `just ci`'s assertions died
    on that DuckDB message. Any future bump would have done the same.
    """


def sdmx_catalog(conn: duckdb.DuckDBPyConnection) -> str:
    """The catalog whose ``sdmx`` schema `conn` reads: its current database.

    A store opened on its own file reads the file's catalog. An edition in a
    shared `EditionEngine` reads its own ``<prefix>__metadata`` catalog, which its
    handle selected with `use_sdmx` (GH-133).
    """
    (catalog,) = first(conn.execute("SELECT current_database()")) or ("memory",)
    return str(catalog)


def use_sdmx(handle: duckdb.DuckDBPyConnection, catalog: str) -> duckdb.DuckDBPyConnection:
    """Point `handle` at `catalog`'s ``sdmx`` schema, so ``sdmx.<table>`` and a bare table name resolve there.

    A DuckDB cursor is a session of its own and does not inherit its parent's
    current database or search path, so every handle a store derives is
    pointed explicitly.

    Returns:
        `handle`, for chaining.
    """
    handle.execute(f'USE "{catalog.replace(chr(34), chr(34) * 2)}".sdmx')
    return handle


def sdmx_cursor(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """A new cursor on `conn`, reading the same ``sdmx`` schema `conn` reads."""
    return use_sdmx(conn.cursor(), sdmx_catalog(conn))


def connection_is_read_only(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether this connection's current database was attached read-only."""
    row = first(conn.execute("SELECT readonly FROM duckdb_databases() WHERE database_name = current_database()"))
    return bool(row[0]) if row else False


def holds_rows(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether any ``sdmx`` table but ``_meta`` has a row — what a rebuild would lose.

    Asks every table rather than ``artefact`` alone, because the store being
    asked about is on *another* schema version, whose tables this build does
    not know.
    """
    tables = conn.execute(
        """
        SELECT table_name FROM duckdb_tables()
        WHERE database_name = current_database() AND schema_name = 'sdmx' AND table_name <> '_meta'
        """
    ).fetchall()
    return any(first(conn.execute(f'SELECT EXISTS (SELECT 1 FROM sdmx."{name}")')) == (True,) for (name,) in tables)


def migrate(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Drop and rebuild the ``sdmx`` schema.

    Everything in it is lost. `LocalRegistry` calls this only for a store with
    nothing in it, or when opened with ``rebuild=True`` (GH-57).

    Returns a fresh cursor with ``search_path = 'sdmx'``.
    """
    conn.execute("DROP SCHEMA IF EXISTS sdmx CASCADE")
    return create_schema(conn)
