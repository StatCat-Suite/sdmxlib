"""One lookup into a reader's intra-message resolution index.

Every reader builds the same thing while parsing: a `dict` from URN string to
the artefact it names, used to turn a reference into a resolved `Ref` without
leaving the message. Both resolvers — `sdmx_ml._common.resolve_ref` and
`sdmx_json._resolve.resolve_urn` — did one `index.get(str(urn))` and stopped
there, which is correct for a maintainable and wrong for an item: the index
holds schemes, and an item URN names something *inside* one.

The consequence was silent (#405). The reference was not dropped — `Ref.urn`
was intact — so a `HierarchicalCode.code` came back unresolved with its codelist
in the same message, and the caller found out on unwrap, at whatever later point
it read `node.code()`. A `Categorisation` showed it in one object: `source()`
returned the dataflow while `target()` raised `UnresolvedRef` for a category in
the message's own scheme.

The readers *do* index one item class — `Concept`, because DSD components
reference concepts — which is why this looked like a design decision rather than
an unfinished loop, and is the same reason `validate_references` expanded
concept schemes and no others (#404).

Indexing every item eagerly is not the answer: `index_item` writes two entries
per item, on every parse, and the readers handle a 579,951-code codelist and a
499,138-node hierarchy. `resolve_item` pays per *reference* instead — a failed
scheme lookup, then an O(1) lookup inside the scheme it names.
"""

from collections.abc import Mapping

from sdmxlib.model._items import resolve_item
from sdmxlib.model.urn import SdmxUrn


def lookup(index: Mapping[str, object], urn: SdmxUrn[object]) -> object | None:
    """The artefact *urn* names, or None.

    Exact match first, which is what every reference to a maintainable gets and
    costs one dict hit — `SdmxUrn.__str__` is cached on the frozen URN, so even
    the key is already built. Only a miss reaches `resolve_item`, and only an
    *item* URN gets anything from it.
    """
    obj = index.get(str(urn))
    if obj is not None:
        return obj
    return resolve_item(index, urn)
