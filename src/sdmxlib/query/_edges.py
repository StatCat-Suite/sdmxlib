"""The declared edge graph, read in both directions.

One `Edge` per storage foreign key. Each is declared **once** and read two
ways — `dsd.components` downward and `component.dsd` upward are the same
`dsd_component.dsd_urn`, not two facts that can drift apart. That is what lets
the compiler keep a single edge table and what makes path-uniqueness counts
meaningful.

**Cardinality is derived, not declared.** A declaration that says "to-one" can
be wrong; a key constraint cannot. Forward (source to target) is to-one exactly
when the target column is unique; backward is to-one exactly when the source
column is. `tests/unit/test_query_edges.py` checks both against the live
schema, along with optionality, because a mis-declared cardinality fails
*silently* — it multiplies or drops rows — where a missing column fails loudly.
See `docs/design/query-language-examples.md` §5 for the trap this prevents.
"""

import attrs


@attrs.frozen
class Edge:
    """One storage foreign key, usable from either end.

    Attributes:
        name: Attribute on the *source* accessor — `component.dsd`. `None`
            when the forward direction is not exposed, which is the case for
            the polymorphic side tables: `annotation.owner_urn` points at
            artefacts, codes and concepts, so one forward name would have to
            mean three different targets.
        source: Table the foreign key lives on, unqualified.
        column: Foreign-key column on `source`. A *tuple* for a composite
            reference, paired positionally with `target_column`.
        target: Table the foreign key points at, unqualified.
        target_column: Column on `target` it points at. Must be unique for the
            forward direction to be to-one; the guard checks this.
        member_of: Which pair, if any, matches by *membership* rather than
            equality — the index into `columns` whose source side is a list.
            `code.ancestor_ids` is the case: an ancestor row is one whose
            `code_id` appears in the child's baked `ancestor_ids`. `None` for
            an ordinary foreign key.
        member_ids: For a membership edge over a nested tree, the source's list
            of the members' *own ids* and the target column it holds —
            ``("ancestor_ids", "node_id")``. The edge joins on paths, because a
            node id may repeat in another branch (#439); this second list keeps
            ``ancestors.id == X`` a membership test on the outer row rather
            than a join, since "an ancestor whose id is X" is exactly "X among
            the ancestors' ids".
        inverse: Attribute on the *target* accessor — `dsd.components`. `None`
            when the reverse direction is not exposed, which is the case for
            the polymorphic side tables whose owner may be any artefact.
        optional: Whether `column` is nullable. Drives the join kind: `LEFT` in
            a projection or under a negation, `INNER` under a positive filter.
    """

    name: str | None
    source: str
    column: str | tuple[str, ...]
    target: str
    target_column: str | tuple[str, ...]
    inverse: str | None = None
    optional: bool = False
    member_of: int | None = None
    member_ids: tuple[str, str] | None = None

    @property
    def columns(self) -> tuple[tuple[str, str], ...]:
        """The reference as `(source column, target column)` pairs.

        Composite references are the rule rather than the exception once you
        look: every tree relation in this schema scopes its self-reference by
        the owning artefact — `code.parent_id` means `code_id` *within one
        codelist* — and `hierarchy_level` is keyed
        `(hierarchy_urn, level_id)`. Declaring a single column for those would
        match across artefacts, which is a wrong answer rather than a missing
        feature.
        """
        source = (self.column,) if isinstance(self.column, str) else self.column
        target = (self.target_column,) if isinstance(self.target_column, str) else self.target_column
        if len(source) != len(target):
            msg = f"{self.source}.{self.name or self.inverse}: {len(source)} source columns, {len(target)} target"
            raise ValueError(msg)
        return tuple(zip(source, target, strict=True))

    def condition(self, source_alias: str, target_alias: str) -> str:
        """The join or correlation condition, from either end.

        One place, because the three emission sites — a `JOIN ... ON` and the
        two correlated `EXISTS` — had each spelled it themselves, and a
        composite edge is where three spellings would have drifted.
        """
        parts = []
        for index, (src, tgt) in enumerate(self.columns):
            if index == self.member_of:
                parts.append(f"{target_alias}.{tgt} = ANY({source_alias}.{src})")
            else:
                parts.append(f"{target_alias}.{tgt} = {source_alias}.{src}")
        return " AND ".join(parts)


#: The edge graph. Grouped by the relation the foreign key lives on.
#:
#: Deliberately *not* auto-derived from the schema: a `*_urn` column does not
#: say which relation it points at (``annotation.owner_urn`` points at any
#: artefact), nor what the edge should be called, nor whether the reverse
#: direction is meaningful to expose. Those are modelling decisions.
EDGES: tuple[Edge, ...] = (
    # ── the extension tables: a 1:1 identity split with `artefact` ──────────
    # Physical layout, not modelling. The maintainable surface is *hoisted*
    # onto every maintainable accessor, so no caller writes these hops — but
    # an accessor whose own edges live on the extension has to cross back to
    # reach its hoisted columns, and an accessor rooted on the spine has to
    # cross out to reach the extension's columns. Both directions are declared
    # for that reason, and both are used only inside `accessors.py`.
    #
    # Declared as two independent *forward* to-one edges rather than one edge
    # with an `inverse`, because `inverse=` means fan-out everywhere else in
    # this graph: `test_reverse_direction_is_to_many` enforces it. A 1:1 has a
    # to-one reverse, so it is not an inverse — it is another edge.
    #
    # Outward from the spine is `optional=True` while inward is not, and that
    # asymmetry is not about nullability: `artefact.urn` is NOT NULL. It is
    # that the *row* may be absent — an artefact that is not a dataflow has no
    # `sdmx.dataflow` row — so the join must be LEFT. No foreign keys are
    # declared in this schema, so nothing can derive that; the optionality
    # guard names the case instead.
    Edge("spine", "dataflow", "urn", "artefact", "urn"),
    Edge("spine", "metadataflow", "urn", "artefact", "urn"),
    Edge("spine", "provision_agreement", "urn", "artefact", "urn"),
    Edge("spine", "categorisation", "urn", "artefact", "urn"),
    Edge("spine", "metadata_set", "urn", "artefact", "urn"),
    Edge("spine", "representation_map", "urn", "artefact", "urn"),
    Edge("spine", "concept_scheme_map", "urn", "artefact", "urn"),
    Edge("spine", "category_scheme_map", "urn", "artefact", "urn"),
    Edge("spine", "organisation_scheme_map", "urn", "artefact", "urn"),
    Edge("spine", "structure_map", "urn", "artefact", "urn"),
    Edge("spine", "metadata_provision_agreement", "urn", "artefact", "urn"),
    Edge("spine", "data_constraint", "urn", "artefact", "urn"),
    Edge("spine", "hierarchy_association", "urn", "artefact", "urn"),
    # Outward, declared only where an accessor needs it. `"artefact"` would be
    # the obvious name for the hop back, but `categorisation.artefact` is
    # already taken by the artefact a categorisation *categorises* — a real
    # edge with a different meaning. Hence `"spine"`.
    #
    # `optional=True` here is currently unfalsifiable by test: the only route
    # through it is `q.dataflow.dsd`, whose root already discriminates on
    # `artefact_type = 'Dataflow'`, so every row reaching the join has its
    # extension row and INNER would answer identically. It is declared for the
    # general case and pinned by `test_optionality_matches_the_schema`, not by
    # a behavioural test — said here so the gap is not mistaken for coverage.
    Edge("dataflow", "artefact", "urn", "dataflow", "urn", optional=True),
    Edge("metadata_set", "artefact", "urn", "metadata_set", "urn", optional=True),
    Edge("metadataflow", "artefact", "urn", "metadataflow", "urn", optional=True),
    Edge("provision_agreement", "artefact", "urn", "provision_agreement", "urn", optional=True),
    Edge("representation_map", "artefact", "urn", "representation_map", "urn", optional=True),
    Edge("concept_scheme_map", "artefact", "urn", "concept_scheme_map", "urn", optional=True),
    Edge("category_scheme_map", "artefact", "urn", "category_scheme_map", "urn", optional=True),
    Edge("organisation_scheme_map", "artefact", "urn", "organisation_scheme_map", "urn", optional=True),
    Edge("structure_map", "artefact", "urn", "structure_map", "urn", optional=True),
    Edge("data_constraint", "artefact", "urn", "data_constraint", "urn", optional=True),
    Edge("hierarchy_association", "artefact", "urn", "hierarchy_association", "urn", optional=True),
    Edge(
        "metadata_provision_agreement",
        "artefact",
        "urn",
        "metadata_provision_agreement",
        "urn",
        optional=True,
    ),
    # ── dsd_component ──────────────────────────────────────────────────────
    Edge("dsd", "dsd_component", "dsd_urn", "artefact", "urn", inverse="components"),
    Edge(
        "concept",
        "dsd_component",
        "concept_urn",
        "concept",
        "concept_urn",
        inverse="components",
        optional=True,
    ),
    Edge(
        "codelist",
        "dsd_component",
        "codelist_urn",
        "artefact",
        "urn",
        optional=True,
    ),
    # ── dataflow ───────────────────────────────────────────────────────────
    # `optional=True` since 2.17: the model field is `Optional` and storage
    # now says so with NULL instead of the empty string that made a required
    # column out of an absent reference.
    Edge("dsd", "dataflow", "dsd_urn", "artefact", "urn", inverse="dataflows", optional=True),
    # ── code / concept / category: item in scheme ──────────────────────────
    Edge("codelist", "code", "codelist_urn", "artefact", "urn", inverse="codes"),
    Edge("scheme", "concept", "scheme_urn", "artefact", "urn", inverse="concepts"),
    Edge("scheme", "category", "scheme_urn", "artefact", "urn", inverse="categories"),
    # A concept's *default* representation. Distinct from a component's own
    # `codelist`, and the pair is why `dsd -> codelist` has two routes with
    # different answers — see the evidence file, "Two routes to a codelist".
    Edge(
        "codelist",
        "concept",
        "codelist_urn",
        "artefact",
        "urn",
        optional=True,
    ),
    # ── the tree relations: self-references, all composite ─────────────────
    #
    # Every tree in this schema scopes its self-reference by the owning
    # artefact: `code.parent_id` means `code_id` *within one codelist*. A
    # single-column edge would match across codelists — a wrong answer rather
    # than a missing feature — which is why `Edge.column` takes a tuple.
    #
    # `ancestors` is the same reference walked transitively, and it is a real
    # edge rather than a verb because the writer bakes the closure: an
    # ancestor row is one whose id appears in the child's `ancestor_ids`. That
    # is a *membership* match, and membership is what makes the forward
    # direction to-many — key uniqueness would say to-one and be wrong.
    Edge(
        "parent",
        "code",
        ("codelist_urn", "parent_id"),
        "code",
        ("codelist_urn", "code_id"),
        inverse="children",
        optional=True,
    ),
    Edge(
        "ancestors",
        "code",
        ("codelist_urn", "ancestor_ids"),
        "code",
        ("codelist_urn", "code_id"),
        inverse="descendants",
        optional=True,
        member_of=1,
    ),
    # Concepts nest within a scheme too — SDMX-ML's `<str:Parent>`, which the
    # XSD calls "the semantic relationships between concepts which occur within
    # a single concept scheme". The column existed from the start and the
    # writer put a constant NULL in it, so this edge could not be declared
    # until #307 populated it.
    Edge(
        "parent",
        "concept",
        ("scheme_urn", "parent_id"),
        "concept",
        ("scheme_urn", "concept_id"),
        inverse="children",
        optional=True,
    ),
    Edge(
        "ancestors",
        "concept",
        ("scheme_urn", "ancestor_ids"),
        "concept",
        ("scheme_urn", "concept_id"),
        inverse="descendants",
        optional=True,
        member_of=1,
    ),
    # The three nested trees join on *path*, not id: an id repeats across
    # branches (#439), and only `(scheme, path)` is unique.
    Edge(
        "parent",
        "category",
        ("scheme_urn", "parent_path"),
        "category",
        ("scheme_urn", "path"),
        inverse="children",
        optional=True,
    ),
    Edge(
        "ancestors",
        "category",
        ("scheme_urn", "ancestor_paths"),
        "category",
        ("scheme_urn", "path"),
        inverse="descendants",
        optional=True,
        member_of=1,
        member_ids=("ancestor_ids", "category_id"),
    ),
    Edge(
        "parent",
        "hierarchy_node",
        ("hierarchy_urn", "parent_path"),
        "hierarchy_node",
        ("hierarchy_urn", "path"),
        inverse="children",
        optional=True,
    ),
    Edge(
        "ancestors",
        "hierarchy_node",
        ("hierarchy_urn", "ancestor_paths"),
        "hierarchy_node",
        ("hierarchy_urn", "path"),
        inverse="descendants",
        optional=True,
        member_of=1,
        member_ids=("ancestor_ids", "node_id"),
    ),
    # ── hierarchy ──────────────────────────────────────────────────────────
    Edge(
        "hierarchy",
        "hierarchy_node",
        "hierarchy_urn",
        "artefact",
        "urn",
        inverse="hcodes",
    ),
    Edge(
        "code",
        "hierarchy_node",
        "code_urn",
        "code",
        "code_urn",
        optional=True,
    ),
    # A node's level, scoped by the hierarchy that declares it. The last of the
    # one-off traversals: `level_id` was a bare string column, so "nodes at the
    # region level" could name the level but not read its label or its index.
    # It is composite for the same reason the tree edges are — `level_id` is
    # unique within a hierarchy, not across them.
    Edge(
        "level",
        "hierarchy_node",
        ("hierarchy_urn", "level_id"),
        "hierarchy_level",
        ("hierarchy_urn", "level_id"),
        inverse="hcodes",
        optional=True,
    ),
    # ── provision / categorisation / metadataflow ──────────────────────────
    Edge(
        "dataflow",
        "provision_agreement",
        "dataflow_urn",
        "artefact",
        "urn",
        inverse="provision_agreements",
        optional=True,
    ),
    Edge(
        # Points at `sdmx.data_provider`, not at the spine. A DataProvider is
        # an *item* of a DataProviderScheme, so its urn never appears in
        # `sdmx.artefact` — the scheme is the artefact. Declared against the
        # spine until #306's coverage pass, where exposing the hop for the
        # first time is what ran it: the join matched zero rows where the
        # correct one matches every row. Nothing had caught it because no
        # accessor reached the edge, and an empty result is not an error.
        "data_provider",
        "provision_agreement",
        "data_provider_urn",
        "data_provider",
        "provider_urn",
        optional=True,
    ),
    # ── organisation items ─────────────────────────────────────────────────
    # `agency` and `data_provider` are items of their schemes, keyed
    # `(scheme_urn, <id>)` like every other item table. Note this is unrelated
    # to `artefact.agency_id`, which is a bare maintainer id with no row to
    # reach — see NOT_EDGES in `tests/unit/test_query_edges.py`.
    Edge("scheme", "agency", "scheme_urn", "artefact", "urn", inverse="agencies"),
    Edge("scheme", "data_provider", "scheme_urn", "artefact", "urn", inverse="data_providers"),
    # A contact hangs off an *agency*, not off an artefact. `DataProvider` also
    # declares contacts in the model and there is nowhere to put them — no
    # `data_provider_contact` table and no writer — so that half is dropped on
    # persist. See the issue linked from `_Contact`.
    # Contacts belong to an *organisation*, so one table serves both and each
    # owner declares its own edge over `owner_urn`. `DataProvider.contacts`
    # had no edge because it had no storage (#309), not because the hop was
    # unwanted.
    # Both are `optional`, and that is the polymorphic owner showing through:
    # a contact's `owner_urn` matches an agency *or* a provider, never both. An
    # inner join on either would drop every contact of the other kind, and
    # selecting both at once would return nothing at all — measured, before
    # these were marked optional.
    Edge(
        "agency",
        "organisation_contact",
        "owner_urn",
        "agency",
        "agency_urn",
        inverse="contacts",
        optional=True,
    ),
    Edge(
        "data_provider",
        "organisation_contact",
        "owner_urn",
        "data_provider",
        "provider_urn",
        inverse="contacts",
        optional=True,
    ),
    Edge("msd", "metadataflow", "msd_urn", "artefact", "urn", optional=True),
    Edge(
        "category",
        "categorisation",
        "target_urn",
        "category",
        "category_urn",
        optional=True,
    ),
    # The two ends of a categorisation, both pointing at `sdmx.artefact` and
    # meaning different things — `artefact` is what was categorised, `category`
    # is what it was categorised *under*. The inverse is what turns "is this in
    # that category" from a bespoke predicate into a path.
    Edge("artefact", "categorisation", "source_urn", "artefact", "urn", inverse="categorisations", optional=True),
    # ── reported metadata ──────────────────────────────────────────────────
    #
    # A `MetadataSet` is a maintainable with an extension, like a dataflow: the
    # spine holds its identity and `sdmx.metadata_set` the structure it was
    # reported against. Its *contents* are two side tables — the reported
    # values, and the artefacts those values describe.
    # ── data_constraint: what a constraint is about, and what it allows ────
    # The provider is a column on the extension table rather than a
    # role-filtered `artefact_ref` row — see the schema comment on
    # `data_constraint` — which keeps it an ordinary to-one edge with derived
    # cardinality (#308). The structures, flows and agreements are several
    # (#422), a table each: each table's rows reach their constraint and the
    # artefact attached, so `dataflow.constraints` crosses one of them. The
    # inverses hang off the extension, since `dataflows` and
    # `provision_agreements` already name hops on `artefact`.
    Edge(
        "data_provider",
        "data_constraint",
        "attachment_data_provider_urn",
        "data_provider",
        "provider_urn",
        optional=True,
    ),
    *(
        Edge("constraint", table, "constraint_urn", "data_constraint", "urn", inverse=inverse)
        for table, inverse in (
            ("data_constraint_dataflow", "dataflows"),
            ("data_constraint_dsd", "dsds"),
            ("data_constraint_provision", "provision_agreements"),
        )
    ),
    Edge("dataflow", "data_constraint_dataflow", "dataflow_urn", "artefact", "urn", inverse="constraints"),
    Edge("dsd", "data_constraint_dsd", "dsd_urn", "artefact", "urn"),
    Edge("provision_agreement", "data_constraint_provision", "provision_urn", "artefact", "urn"),
    Edge("constraint", "cube_region", "constraint_urn", "artefact", "urn", inverse="cube_regions"),
    Edge(
        "region",
        "region_key_value",
        ("constraint_urn", "region_idx"),
        "cube_region",
        ("constraint_urn", "region_idx"),
        inverse="key_values",
    ),
    # ── hierarchy_association ──────────────────────────────────────────────
    # Three slots, already columns on the extension — never blocked, contrary
    # to #308's first reading.
    Edge(
        "hierarchy",
        "hierarchy_association",
        "linked_hierarchy_urn",
        "artefact",
        "urn",
        inverse="associations",
        optional=True,
    ),
    Edge("linked_object", "hierarchy_association", "linked_object_urn", "artefact", "urn", optional=True),
    # The same reference, read as the component it usually names. `Dimension`
    # is an item class, so its URN is never in `sdmx.artefact` and the edge
    # above matches nothing for the ordinary case — the association exists to
    # bind a hierarchy to a dimension. `dsd_component` keys on
    # (dsd_urn, component_id), which is what schema 2.16 bakes out of
    # `linked_object_urn` (#372).
    Edge(
        "component",
        "hierarchy_association",
        ("linked_object_owner_urn", "linked_object_item_id"),
        "dsd_component",
        ("dsd_urn", "component_id"),
        inverse="hierarchy_associations",
        optional=True,
    ),
    Edge("context_object", "hierarchy_association", "context_object_urn", "artefact", "urn", optional=True),
    # ── hierarchy_applicability (#372) ─────────────────────────────────────
    # The view's three references. It is derived, so these are the *only*
    # hops out of it — nothing points back in, and there is no inverse to
    # declare: a relation nobody stores cannot be somebody's fan-out.
    Edge("dataflow", "hierarchy_applicability", "dataflow_urn", "artefact", "urn", optional=True),
    Edge("hierarchy", "hierarchy_applicability", "hierarchy_urn", "artefact", "urn", optional=True),
    Edge("association", "hierarchy_applicability", "association_urn", "artefact", "urn", optional=True),
    Edge(
        "component",
        "hierarchy_applicability",
        ("dsd_urn", "component_id"),
        "dsd_component",
        ("dsd_urn", "component_id"),
        optional=True,
    ),
    # ── metadata_provision_agreement ───────────────────────────────────────
    # The metadata mirror of ProvisionAgreement, and now wired on both sides.
    # `metadata_provider_urn` used to point at a relation that did not exist —
    # `TEXT NOT NULL`, populated, and unresolvable by anything (#315).
    Edge(
        "metadataflow",
        "metadata_provision_agreement",
        "metadataflow_urn",
        "artefact",
        "urn",
        inverse="metadata_provision_agreements",
        optional=True,
    ),
    Edge(
        # Points at `sdmx.metadata_provider`, **not** at the spine — the same
        # trap the data twin fell into and that #306's coverage pass caught:
        # a MetadataProvider is an *item* of a MetadataProviderScheme, so its
        # urn never appears in `sdmx.artefact`. Declared against the spine, the
        # join matches zero rows, and an empty result is not an error.
        "metadata_provider",
        "metadata_provision_agreement",
        "metadata_provider_urn",
        "metadata_provider",
        "provider_urn",
        optional=True,
    ),
    Edge(
        "scheme",
        "metadata_provider",
        "scheme_urn",
        "artefact",
        "urn",
        inverse="metadata_providers",
    ),
    # ── data consumers and organisation units (#469) ───────────────────────
    Edge("scheme", "data_consumer", "scheme_urn", "artefact", "urn", inverse="data_consumers"),
    Edge("scheme", "organisation_unit", "scheme_urn", "artefact", "urn", inverse="organisation_units"),
    # A unit's parent is a unit of the same scheme, scoped as a code's is.
    Edge(
        "parent",
        "organisation_unit",
        ("scheme_urn", "parent_id"),
        "organisation_unit",
        ("scheme_urn", "unit_id"),
        inverse="children",
        optional=True,
    ),
    # Their contacts, optional for the polymorphic owner, as above.
    Edge(
        "data_consumer",
        "organisation_contact",
        "owner_urn",
        "data_consumer",
        "consumer_urn",
        inverse="contacts",
        optional=True,
    ),
    Edge(
        "organisation_unit",
        "organisation_contact",
        "owner_urn",
        "organisation_unit",
        "unit_urn",
        inverse="contacts",
        optional=True,
    ),
    # ── representation_map: value translation, N:M (#422) ──────────────────
    # Endpoints and mappings are children of the *extension* row, not the
    # spine: `targets` is already `metadata_set`'s inverse into `artefact`,
    # and a hop name resolves to the first edge that carries it. Reached as
    # `("representation_map", "sources")`, the way `_Dataflow.dsd` crosses its
    # extension.
    *(
        Edge("representation_map", table, "representation_map_urn", "representation_map", "urn", inverse=inverse)
        for table, inverse in (
            ("representation_map_source", "sources"),
            ("representation_map_target", "targets"),
            ("representation_mapping", "mappings"),
        )
    ),
    # An endpoint is a codelist (or value list) or a data type; storage holds
    # both now, so the hop is plainly `codelist` and null for a data type.
    Edge("codelist", "representation_map_source", "codelist_urn", "artefact", "urn", optional=True),
    Edge("codelist", "representation_map_target", "codelist_urn", "artefact", "urn", optional=True),
    # ── the other three maps: endpoints and ordered pairs (#395) ───────────
    # Endpoints are `source` / `target` here, not role-plus-class the way
    # `representation_map`'s are: those two are both Codelist and needed
    # telling apart, while these name one class each and can say so plainly.
    Edge("source", "concept_scheme_map", "source_urn", "artefact", "urn", optional=True),
    Edge("target", "concept_scheme_map", "target_urn", "artefact", "urn", optional=True),
    Edge("source", "category_scheme_map", "source_urn", "artefact", "urn", optional=True),
    Edge("target", "category_scheme_map", "target_urn", "artefact", "urn", optional=True),
    # An organisation scheme map's ends are any organisation scheme, some of
    # them unmodelled, so they land on the spine and no one scheme class (#448).
    Edge("source", "organisation_scheme_map", "source_urn", "artefact", "urn", optional=True),
    Edge("target", "organisation_scheme_map", "target_urn", "artefact", "urn", optional=True),
    Edge("source", "structure_map", "source_urn", "artefact", "urn", optional=True),
    Edge("target", "structure_map", "target_urn", "artefact", "urn", optional=True),
    # `item_map` serves every scheme-map kind and needs *one* edge, not two.
    # `organisation_contact`'s pair works because its owners are different
    # tables (`agency`, `data_provider`); the scheme maps are maintainables
    # living in `artefact`, so an edge each here would all be named
    # `artefact.item_maps` and the reverse hop would be ambiguous. One edge
    # serves them all, and each parent accessor's `__constraint__` on
    # `artefact_type` is what tells them apart (#395).
    Edge("scheme_map", "item_map", "map_urn", "artefact", "urn", inverse="item_maps"),
    Edge("structure_map", "component_map", "structure_map_urn", "artefact", "urn", inverse="component_maps"),
    # ── dsd_group: attribute attachment by partial key ─────────────────────
    # A `Group` names a subset of the key that attributes may hang off. It has
    # no urn of its own, so it is keyed `(dsd_urn, group_id)` and every edge
    # into or out of it is composite.
    Edge("dsd", "dsd_group", "dsd_urn", "artefact", "urn", inverse="groups"),
    # `group.dimensions` is the *link* row, and `group_dimension.dimension`
    # then reaches the real `dsd_component`. Two hops rather than one because
    # `GroupDimension` is a genuine SDMX class holding only a reference — the
    # accessor spells the pair as one compound path so a caller writes
    # `q.dsd.groups.dimensions.id` and never names the junction.
    Edge(
        "group",
        "group_dimension",
        ("dsd_urn", "group_id"),
        "dsd_group",
        ("dsd_urn", "group_id"),
        inverse="dimensions",
    ),
    Edge(
        "dimension",
        "group_dimension",
        ("dsd_urn", "dimension_id"),
        "dsd_component",
        ("dsd_urn", "component_id"),
    ),
    # ── metadata_attribute: the MSD's declared slots ───────────────────────
    # The declared half of reference metadata, paired with `reported_attribute`
    # which is the populated half. `metadata_attributes` rather than
    # `attributes` for the inverse, because `(artefact, "attributes")` is
    # already the DSD facet and one accessor cannot carry two meanings for one
    # name — the guard in `test_query_edges.py` would refuse it.
    Edge("msd", "metadata_attribute", "msd_urn", "artefact", "urn", inverse="metadata_attributes"),
    Edge("concept", "metadata_attribute", "concept_urn", "concept", "concept_urn", optional=True),
    Edge("codelist", "metadata_attribute", "codelist_urn", "artefact", "urn", optional=True),
    # The fourth tree in this schema, after code, category and hierarchy_node.
    # Composite for the same reason they are, and on path as the other two
    # nested ones are: an attribute is unique by path within one MSD (#439).
    Edge(
        "parent",
        "metadata_attribute",
        ("msd_urn", "parent_path"),
        "metadata_attribute",
        ("msd_urn", "path"),
        inverse="children",
        optional=True,
    ),
    Edge(
        "ancestors",
        "metadata_attribute",
        ("msd_urn", "ancestor_paths"),
        "metadata_attribute",
        ("msd_urn", "path"),
        inverse="descendants",
        optional=True,
        member_of=1,
        member_ids=("ancestor_ids", "attribute_id"),
    ),
    Edge("structure", "metadata_set", "structure_urn", "artefact", "urn", inverse="metadata_sets"),
    Edge("metadata_set", "metadata_value", "set_urn", "artefact", "urn", inverse="reported_attributes"),
    Edge("metadata_set", "metadata_target", "set_urn", "artefact", "urn", inverse="targets"),
    # What a target points at — any identifiable artefact, which is the whole
    # point of reference metadata. Distinct from `set`, and both live on the
    # same row, so naming them apart is what keeps "the set this target belongs
    # to" and "the artefact it describes" from collapsing into one hop.
    # The inverse names the question reference metadata is usually asked from
    # the *other* side: "what has been said about this artefact".
    Edge("artefact", "metadata_target", "target_urn", "artefact", "urn", inverse="described_by"),
    # ── annotations: one hub column, three typed edges ─────────────────────
    #
    # `annotation.owner_urn` is a genuine hub — it legitimately points at any
    # artefact *and* at items. Declaring one polymorphic edge would make
    # everything reachable from everything, which is the measurement that made
    # bare column references unresolvable. Three *typed* edges keep the graph
    # honest instead: `code.annotations` and `dsd.annotations` are different
    # edges, and there is no path from a code to a DSD through annotations.
    Edge(None, "annotation", "owner_urn", "artefact", "urn", inverse="annotations"),
    Edge(None, "annotation", "owner_urn", "code", "code_urn", inverse="annotations"),
    Edge(None, "annotation", "owner_urn", "concept", "concept_urn", inverse="annotations"),
    Edge(None, "annotation", "owner_urn", "hierarchy_node", "node_urn", inverse="annotations"),
    # An annotation's URLs (#422): several, each with a language.
    Edge("annotation", "annotation_url", "annotation_id", "annotation", "id", inverse="urls"),
)


@attrs.frozen
class Facet:
    """A named, pre-filtered view of a to-many edge.

    `dsd.dimensions`, `dsd.attributes` and `dsd.measures` are one storage edge
    — `dsd_component.dsd_urn` — plus a constant on `component_type`. Declaring
    them as facets rather than as separate edges keeps the edge graph honest
    about the schema (there is one foreign key) while letting the accessor be
    as precise as the domain model.

    It also removes the temptation to write
    ``dsd.components.any(component.role == "dimension", ...)`` at every call
    site, which the design names as the worse spelling: it buries the role in a
    predicate where a reader has to notice it.

    Attributes:
        name: Attribute exposed on the parent accessor.
        base: `Edge.inverse` of the unfiltered edge this refines.
        column: Column on the child carrying the discriminator.
        values: Accepted values. A tuple because a facet may cover several
            stored kinds even when the domain exposes one name.
    """

    name: str
    base: str
    column: str
    values: tuple[str, ...]


#: Pre-filtered fan-outs. The values are `component_type` as written by
#: `storage.writers` — SDMX class names, not lowercased roles.
#:
#: These mirror `DataStructure`'s named collections exactly: `dimensions` is
#: ``Dimension`` alone, because `TimeDimension` and `MeasureDimension` are
#: separate scalar fields on the domain object rather than members of
#: ``dsd.dimensions``.
FACETS: tuple[Facet, ...] = (
    Facet("dimensions", "components", "component_type", ("Dimension",)),
    Facet("attributes", "components", "component_type", ("Attribute",)),
    Facet("measures", "components", "component_type", ("Measure",)),
)


def facets_of(inverse: str) -> tuple[Facet, ...]:
    """Facets refining the fan-out named *inverse*."""
    return tuple(f for f in FACETS if f.base == inverse)


def edges_from(table: str) -> tuple[Edge, ...]:
    """Edges whose foreign key lives on *table* and expose a forward name."""
    return tuple(e for e in EDGES if e.source == table and e.name is not None)


def edges_into(table: str) -> tuple[Edge, ...]:
    """Edges pointing at *table* and exposing a reverse attribute.

    These are the to-many directions, and the ones that make a path change
    cardinality.
    """
    return tuple(e for e in EDGES if e.target == table and e.inverse is not None)
