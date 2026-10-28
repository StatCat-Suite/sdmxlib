"""Fingerprints: what a bundle holds, independent of the bytes that hold it (GH-127).

A token names a publication; two builds from the same inputs get different
tokens and different file bytes. A fingerprint names the *content*, so equal
content fingerprints equal across rebuilds, file layouts and DuckDB releases,
and a result cache can key on it. Four levels, each defined over text whose
form `CANONICAL_FORM` versions (design §4.4):

- **table**: over every row of a binding's table, the sum of each row's MD5,
  taken modulo 2^62, then ``:`` and the row count. Row order does not move it,
  a value moved between rows or a duplicated row does. The row text is
  DuckDB's ``to_json`` of the row, columns in table order.
- **artefact**: the SHA-256 of `canonical_json`.
- **structure**: per dataflow, the SHA-256 of the artefact fingerprints of its
  closure, in URN order, joined by newlines. The closure is everything the
  dataflow and its data structure reach, plus every artefact that refers to
  either (constraints, provision agreements, hierarchy associations: they point
  at the dataflow, not the reverse) and everything those reach.
- **bundle**: the SHA-256 of ``artefacts:``, each artefact fingerprint in URN
  order, ``bindings:``, then the dataflow URN, a tab and the table
  fingerprint per binding in dataflow order, joined by newlines.
"""

import hashlib
from collections.abc import Iterable, Mapping

import duckdb

from sdmxlib.bundle._canonical import canonical_json
from sdmxlib.model._items import owner_classes, scheme_urn_of
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.urn import SdmxUrn

#: Each row's MD5 is taken modulo 2^62, so a HUGEINT sum cannot overflow below 2^66 rows.
_TABLE = """
    SELECT coalesce(sum((md5_number_lower(to_json(t)) % 4611686018427387904)::HUGEINT), 0)::VARCHAR
        || ':' || count(*)::VARCHAR
    FROM {relation} AS t
"""


def table_fingerprint(conn: duckdb.DuckDBPyConnection, binding: DataflowBinding) -> str:
    """The table fingerprint of a binding's table, ``<sum>:<count>``.

    Args:
        conn: A connection on which the binding's database is attached.
        binding: The binding whose table to read.

    Returns:
        The fingerprint.
    """
    relation = ".".join(_quoted(part) for part in (binding.database_role, *binding.table_location.split(".")))
    row = conn.execute(_TABLE.format(relation=relation)).fetchone()
    if row is None:
        msg = f"the fingerprint of {relation} returned no row"
        raise RuntimeError(msg)
    fingerprint: str = row[0]
    return fingerprint


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def artefact_fingerprint(artefact: object) -> str:
    """The SHA-256 of an artefact's canonical text, lowercase hex."""
    return hashlib.sha256(canonical_json(artefact)).hexdigest()


def structure_fingerprint(closure: Iterable[str], fingerprints: Mapping[str, str]) -> str:
    """The structure fingerprint over `closure`'s artefact fingerprints, in URN order."""
    return hashlib.sha256("\n".join(fingerprints[urn] for urn in sorted(closure)).encode()).hexdigest()


def bundle_fingerprint(artefacts: Mapping[str, str], tables: Mapping[str, str]) -> str:
    """The bundle fingerprint: every artefact fingerprint, then every binding's table fingerprint.

    Args:
        artefacts: Artefact fingerprint by URN.
        tables: Table fingerprint by dataflow URN.

    Returns:
        Lowercase hex SHA-256.
    """
    lines = ["artefacts:", *(artefacts[urn] for urn in sorted(artefacts))]
    lines += ["bindings:", *(f"{urn}\t{tables[urn]}" for urn in sorted(tables))]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


class References:
    """The store's references between maintainable artefacts, item references folded onto their schemes.

    ``sdmx.artefact_ref`` holds one row per reference, often to an item (a
    code, a concept, a dimension); this folds each onto the artefact holding
    the item, and keeps only artefacts the store holds. On the census bundle
    that turns 2,161,633 rows into the few hundred edges a closure walks.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, present: Iterable[str]) -> None:
        self._present = frozenset(present)
        rows = conn.execute(
            "SELECT source_urn, regexp_extract(target_urn, '^[^)]*\\)', 0) AS scheme, any_value(target_urn) "
            "FROM sdmx.artefact_ref GROUP BY ALL"
        ).fetchall()
        self._out: dict[str, set[str]] = {}
        self._in: dict[str, set[str]] = {}
        for source, scheme, target in rows:
            held = self._held(scheme, target)
            if source in self._present and held is not None and held != source:
                self._out.setdefault(source, set()).add(held)
                self._in.setdefault(held, set()).add(source)

    def _held(self, scheme: str, target: str) -> str | None:
        """The stored artefact a reference to `target` lands in, or None."""
        if target in self._present:
            return target
        if scheme in self._present:
            return scheme
        try:
            urn = SdmxUrn.parse(target)
        except ValueError:
            return None
        for owner in owner_classes(urn.artefact_class):
            candidate = str(scheme_urn_of(urn, owner))
            if candidate in self._present:
                return candidate
        return None

    def reach(self, start: Iterable[str]) -> set[str]:
        """`start` and everything it refers to, transitively."""
        seen: set[str] = set()
        pending = [urn for urn in start if urn in self._present]
        while pending:
            urn = pending.pop()
            if urn not in seen:
                seen.add(urn)
                pending.extend(self._out.get(urn, ()))
        return seen

    def referring(self, urn: str) -> set[str]:
        """The stored artefacts that refer to `urn`."""
        return set(self._in.get(urn, ()))

    def closure(self, dataflow: str, structures: Iterable[str]) -> set[str]:
        """A dataflow's structural closure.

        What the dataflow and its structures reach, every artefact referring
        to one of them, and what those reach. Another dataflow of the same
        data structure refers to it, so it is in the closure.
        """
        anchors = {dataflow, *structures}
        referring = {source for anchor in anchors for source in self.referring(anchor)}
        return self.reach(anchors | referring)
