"""Nested items keyed by path — the identity SDMX gives them (#439).

A `Category`, a `HierarchicalCode` and a `MetadataAttribute` nest, and SDMX
names a nested item by its **path**: the URN of a category three levels down is
``Category=AG:CS(1.0).ECON.TRADE.GDP``, and every XSD identity constraint on
these items is sibling-scoped. A leaf id may therefore repeat in another
branch, and real registries do repeat it — Eurostat files ``hsw_ac`` under four
parents of one category scheme, OECD puts ``MR`` both at a hierarchy's root and
under ``TYPO_METRO``.

The three schemes used to index by leaf id, so a repeat overwrote its namesake
and every lookup answered for whichever came last. This index is the one
shared replacement, and its lookup rules are pysdmx's:

- ``index["ECON.TRADE.GDP"]`` walks the path; a bare id is a root.
- ``index.by_id("GDP")`` is every node carrying that leaf id, at any depth —
  the question a caller who does not know the parents is really asking, and
  one that may have several answers.

Sibling duplicates are invalid in every SDMX version, so they never get a
second path; `duplicate_paths` keeps them so the rules can say so instead
of the index silently keeping one.
"""

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import NamedTuple

from sdmxlib.model.collections import ItemList, SupportsId


def join_path(parent: str | None, item_id: str) -> str:
    """The path of *item_id* under *parent* — ``None`` for a root."""
    return item_id if parent is None else f"{parent}.{item_id}"


def parent_path(path: str) -> str | None:
    """The path of *path*'s parent, or ``None`` for a root.

    SDMX's ``IDType`` excludes ``.``, so a dot is always nesting.
    """
    head, dot, _ = path.rpartition(".")
    return head if dot else None


class PathIndex[T: SupportsId]:
    """A tree of items, indexed by path, in pre-order.

    Built once from the roots and a children accessor; it does not observe
    later mutation of the tree, exactly as the leaf-keyed `_flat` it replaces
    did not.
    """

    __slots__ = ("_by_leaf", "_children", "_duplicates", "_node", "_nodes", "_parent", "_path_of")

    def __init__(self, roots: Iterable[T], children: Callable[[T], Iterable[T]]) -> None:
        self._node: dict[str, T] = {}
        self._parent: dict[str, str | None] = {}
        self._children: dict[str, list[str]] = {}
        self._by_leaf: dict[str, list[str]] = {}
        self._path_of: dict[int, str] = {}
        self._duplicates: list[str] = []
        root_paths: list[str] = []
        self._children[""] = root_paths
        stack: list[tuple[T, str | None]] = [(node, None) for node in reversed(list(roots))]
        while stack:
            node, parent = stack.pop()
            path = join_path(parent, node.id)
            if path in self._node:
                self._duplicates.append(path)
                continue
            self._node[path] = node
            self._parent[path] = parent
            self._children[path] = []
            self._children["" if parent is None else parent].append(path)
            self._by_leaf.setdefault(node.id, []).append(path)
            self._path_of[id(node)] = path
            stack.extend((child, path) for child in reversed(list(children(node))))
        self._nodes: tuple[T, ...] = tuple(self._node.values())

    # ── lookup ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._node)

    def __contains__(self, path: object) -> bool:
        return path in self._node

    def __getitem__(self, path: str) -> T:
        try:
            return self._node[path]
        except KeyError:
            raise KeyError(path) from None

    def get(self, path: str) -> T | None:
        """The node at *path*, or ``None``."""
        return self._node.get(path)

    def by_id(self, item_id: str) -> ItemList[T]:
        """Every node whose own id is *item_id*, in pre-order."""
        return ItemList([self._node[p] for p in self._by_leaf.get(item_id, ())])

    def paths_with_id(self, item_id: str) -> tuple[str, ...]:
        """The path of every node whose own id is *item_id*, in pre-order."""
        return tuple(self._by_leaf.get(item_id, ()))

    def path_of(self, node: T) -> str:
        """The path of *node*, which must be a node of this tree.

        Raises:
            KeyError: If *node* is not in this tree — compared by identity, so
                an equal node from another tree is not mistaken for this one.
        """
        try:
            return self._path_of[id(node)]
        except KeyError:
            msg = f"{node.id!r} is not a node of this tree"
            raise KeyError(msg) from None

    def paths(self) -> Iterator[str]:
        """Every path, in pre-order."""
        return iter(self._node)

    def nodes(self) -> tuple[T, ...]:
        """Every node, in pre-order — built once, so a length is O(1)."""
        return self._nodes

    @property
    def duplicate_paths(self) -> tuple[str, ...]:
        """Paths declared more than once — sibling duplicates, kept for validation."""
        return tuple(self._duplicates)

    # ── structure ─────────────────────────────────────────────────────────

    def parent(self, path: str) -> str | None:
        """The parent path of *path*; ``None`` for a root.

        Raises:
            KeyError: If *path* is not in the tree.
        """
        if path not in self._node:
            raise KeyError(path)
        return self._parent[path]

    def children(self, path: str) -> tuple[str, ...]:
        """The child paths of *path*, in declaration order."""
        return tuple(self._children.get(path, ()))

    def roots(self) -> tuple[str, ...]:
        """The root paths, in declaration order."""
        return tuple(self._children[""])

    def ancestors(self, path: str) -> tuple[str, ...]:
        """The ancestor paths of *path*, root-first, excluding itself."""
        out: list[str] = []
        current = self._parent.get(path)
        while current is not None:
            out.append(current)
            current = self._parent[current]
        out.reverse()
        return tuple(out)

    def descendants(self, path: str, *, depth: int | None = None) -> tuple[str, ...]:
        """The descendant paths of *path* in pre-order, excluding itself."""
        out: list[str] = []
        stack: list[tuple[str, int]] = [(child, 1) for child in reversed(self._children.get(path, ()))]
        while stack:
            current, hops = stack.pop()
            if depth is not None and hops > depth:
                continue
            out.append(current)
            stack.extend((child, hops + 1) for child in reversed(self._children[current]))
        return tuple(out)

    def depth(self, path: str) -> int:
        """The depth of *path*; a root is 0.

        Counted along the parents rather than the dots in the path: an id read
        from a malformed document may hold a dot, which the rules refuse
        but this must not miscount.
        """
        return len(self.ancestors(path))


class PathNavigation[T: SupportsId]:
    """The SKOS-style navigation every nested scheme offers, keyed by path.

    `CategoryScheme`, `Hierarchy` and `MetadataStructure` each repeated these
    thirteen methods over their own leaf-keyed dicts; they now share one
    implementation over a `PathIndex`. Every *path* argument is a dotted path,
    and a bare id names a root — ``narrower("ECON")`` and
    ``narrower("ECON.TRADE")`` both work, ``narrower("TRADE")`` does not when
    ``TRADE`` is nested. `by_id` is the way in for a caller who has only the
    leaf.

    Every map is keyed by path and holds paths, so two nodes sharing a leaf id
    never collide in one.
    """

    __slots__ = ()

    @property
    def _paths(self) -> PathIndex[T]:
        raise NotImplementedError

    # ── lookup ────────────────────────────────────────────────────────────

    def __getitem__(self, path: str) -> T:
        """The item at *path* — a dotted path, or a root's bare id.

        Raises:
            KeyError: If no item has this path — naming the paths it could
                mean when *path* is a node's id rather than its path.
        """
        self._refuse_bare_id(path)
        return self._paths[path]

    def __contains__(self, path: object) -> bool:
        return path in self._paths

    def get(self, path: str) -> T | None:
        """The item at *path*, or ``None``."""
        return self._paths.get(path)

    def by_id(self, item_id: str) -> ItemList[T]:
        """Every item whose own id is *item_id*, at any depth, in pre-order."""
        return self._paths.by_id(item_id)

    def path_of(self, item: T) -> str:
        """The dotted path of *item*, which must belong to this scheme.

        Raises:
            KeyError: If *item* is not one of this scheme's items.
        """
        return self._paths.path_of(item)

    def _refuse_bare_id(self, path: str) -> None:
        """Raise when *path* is no path here but is some node's own id — see `refuse_bare_id`."""
        index = self._paths
        if path not in index:
            refuse_bare_id(path, index.paths_with_id(path), type(self).__name__)

    # ── SKOS/XKOS hierarchy navigation ────────────────────────────────────

    def leaves(self) -> ItemList[T]:
        """Items with no children, in pre-order."""
        index = self._paths
        return ItemList([index[p] for p in index.paths() if not index.children(p)])

    def narrower(self, path: str) -> ItemList[T]:
        """Direct children of *path*; empty when *path* is not in the scheme.

        Raises:
            KeyError: If *path* is a node's id rather than its path (#514).
        """
        self._refuse_bare_id(path)
        index = self._paths
        return ItemList([index[p] for p in index.children(path)])

    def broader(self, path: str) -> T | None:
        """Direct parent of *path*; ``None`` for a root or an unknown path.

        Raises:
            KeyError: If *path* is a node's id rather than its path (#514).
        """
        self._refuse_bare_id(path)
        index = self._paths
        parent = index.parent(path) if path in index else None
        return None if parent is None else index[parent]

    def broader_transitive(self, path: str, *, depth: int | None = None, include_self: bool = False) -> ItemList[T]:
        """All ancestors of *path*, root-first. *depth* bounds the hops.

        *include_self* puts the item itself last, keeping the result
        root-first. Empty for an unknown path.

        Raises:
            KeyError: If *path* is a node's id rather than its path (#514).
        """
        self._refuse_bare_id(path)
        index = self._paths
        if path not in index:
            return ItemList([])
        ancestors = index.ancestors(path)
        if depth is not None:
            ancestors = ancestors[len(ancestors) - depth :] if depth > 0 else ()
        found = [index[p] for p in ancestors]
        if include_self:
            found.append(index[path])
        return ItemList(found)

    def narrower_transitive(self, path: str, *, depth: int | None = None, include_self: bool = False) -> ItemList[T]:
        """All descendants of *path* in pre-order. *depth* bounds the hops.

        *include_self* puts the item itself first. Empty for an unknown path.

        Raises:
            KeyError: If *path* is a node's id rather than its path (#514).
        """
        self._refuse_bare_id(path)
        index = self._paths
        if path not in index:
            return ItemList([])
        found = [index[p] for p in index.descendants(path, depth=depth)]
        if include_self:
            found.insert(0, index[path])
        return ItemList(found)

    def depth_of(self, path: str) -> int:
        """Depth of *path* in the tree; a root is 0.

        Raises:
            KeyError: If *path* is not in the scheme.
        """
        self._refuse_bare_id(path)
        index = self._paths
        if path not in index:
            msg = f"{path!r} is not a path in {type(self).__name__}"
            raise KeyError(msg)
        return index.depth(path)

    # ── bulk projections ──────────────────────────────────────────────────

    def depths(self) -> dict[str, int]:
        """``{path: depth}`` for every item; a root is 0."""
        index = self._paths
        return {p: index.depth(p) for p in index.paths()}

    def broader_map(self) -> dict[str, str | None]:
        """``{path: parent_path}`` for every item; ``None`` for roots."""
        index = self._paths
        return {p: index.parent(p) for p in index.paths()}

    def narrower_map(self) -> dict[str, tuple[str, ...]]:
        """``{path: (child_path, ...)}`` for every item, in declaration order."""
        index = self._paths
        return {p: index.children(p) for p in index.paths()}

    def ancestors_map(self) -> dict[str, tuple[str, ...]]:
        """``{path: (ancestor_path, ...)}`` root-first, excluding the item."""
        index = self._paths
        return {p: index.ancestors(p) for p in index.paths()}

    def descendants_map(self) -> dict[str, tuple[str, ...]]:
        """``{path: (descendant_path, ...)}`` in pre-order, excluding the item."""
        index = self._paths
        return {p: index.descendants(p) for p in index.paths()}

    def inherit_along_broader[V](self, values: dict[str, V]) -> dict[str, V]:
        """Propagate ``values`` (keyed by path) down the parent chain.

        Each item takes the first value found on itself or its nearest
        ancestor; items with none are omitted.
        """
        index = self._paths
        result: dict[str, V] = {}
        for path in index.paths():
            current: str | None = path
            while current is not None:
                if current in values:
                    result[path] = values[current]
                    break
                current = index.parent(current)
        return result


def sibling_duplicates[T: SupportsId](scheme: PathNavigation[T]) -> tuple[str, ...]:
    """Paths *scheme* declares twice — two siblings sharing an id.

    Invalid in every SDMX version (every XSD identity constraint on these items
    is sibling-scoped), and the one repeat a path cannot tell apart. A
    package-internal function rather than a method, so the rules can ask
    without the question appearing on every scheme's public surface.
    """
    return scheme._paths.duplicate_paths


class TreeRow[T](NamedTuple):
    """One nested item as the store holds it — see `tree_rows`."""

    node: T
    path: str
    parent_path: str | None
    depth: int
    #: The ancestors' own ids, root-first — what ``ancestors.id == X`` asks.
    ancestor_ids: list[str]
    #: The ancestors' paths, root-first — what the ``ancestors`` edge joins on.
    ancestor_paths: list[str]


def tree_rows[T: SupportsId](scheme: PathNavigation[T]) -> Iterator[TreeRow[T]]:
    """Every item of *scheme* with its path and baked closure, in pre-order.

    The storage writers' one source for a nested scheme's rows, so the three
    tables cannot disagree on what a path or an ancestor list is. Each item's
    ancestors extend its parent's, so the closure costs one tuple per item
    rather than a walk to the root — 499,138 nodes in the 2021 Census Profile's largest
    hierarchy.
    """
    index = scheme._paths
    closure: dict[str, tuple[list[str], list[str]]] = {}
    for path in index.paths():
        node = index[path]
        parent = index.parent(path)
        if parent is None:
            ids: list[str] = []
            paths: list[str] = []
        else:
            parent_ids, parent_paths = closure[parent]
            ids = [*parent_ids, index[parent].id]
            paths = [*parent_paths, parent]
        closure[path] = (ids, paths)
        yield TreeRow(node, path, parent, len(paths), ids, paths)


def refuse_bare_id(key: str, paths: Sequence[str], owner: str) -> None:
    """Raise if *key* — not a path in *owner* — is the id of the nodes at *paths*.

    Trees are keyed by path since #439, so a node below the roots is
    ``"CA.ON"``, not ``"ON"``. Code written before then still passes the id,
    and a walk answering "absent" for it — ``None``, empty — reads as a root or
    a leaf rather than as the stale call it is (#514). An unknown key that is
    *nobody's* id stays absent; only the id of a node somewhere is refused,
    naming where it is.

    Raises:
        KeyError: If *paths* is non-empty.
    """
    if not paths:
        return
    msg = (
        f"{key!r} is a node id, not a path in {owner}: trees are keyed by path, dotted from a root "
        f"(#439). It is at {', '.join(repr(p) for p in paths)}; by_id({key!r}) gives every node with that id."
    )
    raise KeyError(msg)
