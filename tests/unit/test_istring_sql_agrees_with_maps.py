"""`istring_sql` must mean exactly what reading the MAP column meant (#380).

The bulk readers stopped selecting `MAP(VARCHAR, VARCHAR)` label columns and
now select `to_json(col)`, decoding with msgspec. That is a *transport* change
with no intended semantic content: DuckDB's MAP → Python `dict` conversion runs
inside `fetchall`, before any of our code, and on the census bundle it was 750 ms of
a 916 ms fetch for 579,951 rows.

A transport change with no intended semantics is exactly the kind that ships a
quiet one. The risk is not the happy path — it is a label containing a quote, a
backslash, a newline, an astral-plane character, or an empty string, where JSON
has an escaping layer the MAP path never had.

So this is a **differential** test in the sense `CLAUDE.md` uses: the two paths
are run over the same value and compared, rather than the JSON path being
compared to an expectation someone wrote by reading the JSON path. The oracle
is the code this replaced.

Hypothesis generates the labels because the interesting inputs here are
character-level and a hand-written list is a list of the escapes its author
happened to think of. The explicit cases below are the ones that were found by
hand first and are kept as regressions.
"""

import duckdb
import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sdmxlib.model.istring import InternationalString
from sdmxlib.storage.resolve import istring_sql, json_to_istring, map_to_istring

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def conn():
    with duckdb.connect() as c:
        yield c


def _both_ways(conn: duckdb.DuckDBPyConnection, mapping: dict[str, str]):
    """The same label as JSON-then-decode and as MAP-then-dict.

    Built through DuckDB rather than in Python, because the whole question is
    what *DuckDB* does to the value on the way out — doing the escaping
    ourselves would be testing our own idea of it.

    The map is **bound**, never interpolated. The first version of this helper
    built `MAP{'a': 'b'}` with Python `repr`, and Hypothesis found the hole in
    one run: `repr("'")` is `"'"`, which DuckDB reads as an *identifier*, so
    the test blew up with a binder error on a value the library handles
    perfectly well. CLAUDE.md's SQL rule — never interpolate values — exists
    for exactly that, and applies to test code building SQL as much as to
    `src/`.
    """
    keys = list(mapping)
    values = [mapping[k] for k in keys]
    expr = "map($k, $v)"
    row = conn.execute(f"SELECT {istring_sql(expr)}, {expr}", {"k": keys, "v": values}).fetchone()
    assert row is not None
    as_json, as_map = row
    return json_to_istring(as_json), map_to_istring(as_map)


#: Labels are free text in SDMX. Drawn from the full BMP plus astral planes,
#: with the JSON-significant characters given their own weight so a shrink
#: lands on the one that broke rather than on whatever else was in the string.
#:
#: Surrogates (`Cs`) are excluded, and not as a convenience: a lone `\ud800`
#: is not valid UTF-8, so DuckDB's binding refuses it as a parameter and the
#: value can never exist in a column. Generating it tests the driver's error
#: handling, not this transport — CLAUDE.md's rule 4, a strategy has to
#: produce input that can actually reach the code under test. Found at 20x
#: width; the default width never drew one.
_SAFE_CHARS = st.characters(exclude_categories=["Cs"])
_TEXT = st.one_of(
    st.text(alphabet=_SAFE_CHARS),
    st.text(alphabet=st.sampled_from('"\\\n\r\t/\x00 é🍁')),
)
_LANG = st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=8)


@given(st.dictionaries(_LANG, _TEXT, max_size=4))
def test_the_json_path_agrees_with_the_map_path(conn, mapping):
    via_json, via_map = _both_ways(conn, mapping)
    assert via_json == via_map
    assert via_json.as_dict() == via_map.as_dict()


@pytest.mark.parametrize(
    ("case", "mapping"),
    [
        ("empty map", {}),
        ("one language", {"en": "Ontario"}),
        ("two languages", {"en": "Quebec", "fr": "Québec"}),
        ("embedded quotes", {"fr": 'Québec "x" / y'}),
        ("backslash", {"en": "a\\b"}),
        ("newline and tab", {"en": "\n\tx"}),
        ("astral plane", {"en": "🍁 maple"}),
        ("empty value", {"en": ""}),
        ("solidus", {"en": "a/b"}),
    ],
)
def test_awkward_labels_round_trip_identically(conn, case, mapping):
    """The hand-found cases, kept as named regressions."""
    via_json, via_map = _both_ways(conn, mapping)
    assert via_json == via_map, case


def test_a_null_column_is_the_shared_empty(conn):
    """NULL is not the same shape as an empty map and must land in the same place."""
    (as_json,) = conn.execute(f"SELECT {istring_sql('NULL::MAP(VARCHAR,VARCHAR)')}").fetchone()
    assert as_json is None
    assert json_to_istring(as_json) is InternationalString.EMPTY
    assert map_to_istring(None) is InternationalString.EMPTY


def test_an_empty_map_is_the_shared_empty(conn):
    """`to_json` renders it `"{}"`, which the decoder must not allocate for."""
    (as_json,) = conn.execute(f"SELECT {istring_sql('MAP{}::MAP(VARCHAR,VARCHAR)')}").fetchone()
    assert as_json == "{}"
    assert json_to_istring(as_json) is InternationalString.EMPTY


def test_a_malformed_payload_raises_rather_than_returning_an_empty_label(conn):
    """A silently-empty label is the failure this must not have.

    Nothing should produce one — the input is DuckDB's own `to_json` — but a
    decoder that swallowed errors would turn a storage bug into missing names
    across a whole codelist, and nobody would see it. Loud is correct here.
    """
    with pytest.raises(msgspec.DecodeError):
        json_to_istring('{"en": ')


def test_istring_sql_names_the_column_it_is_given():
    """It interpolates an identifier and never a value — CLAUDE.md "SQL style"."""
    assert istring_sql("name") == "to_json(name)"
    assert istring_sql("description") == "to_json(description)"
