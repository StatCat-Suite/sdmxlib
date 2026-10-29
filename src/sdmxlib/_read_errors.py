"""What reading observations raises, in sdmxlib's terms (GH-247).

Two failures, two exceptions:

1. **The bytes are not a readable data message** — truncated JSON, a CSV row
   DuckDB cannot split, a missing column: `SdmxParseError`, as `sl.parse`
   raises for a structure message. The library's own error is its
   ``__cause__``.
2. **The message reads, but a value does not fit its component's type** —
   text where the DSD declares a number or a date: ``ValueError``, naming the
   structure and `.validate()`, the one message whether the value is found
   while the file is read or when a query casts its result.

DuckDB parses every SDMX-CSV file sdmxlib reads, `scan_csv` and a REST
`obs()` response alike. Where sdmxlib runs the read itself
(`reading_csv`), any DuckDB failure of that statement is about the file. A
scanned path is a view that DuckDB reads when a query runs, so the query
terminals convert too (`csv_errors`, `converting`), and there only an error
DuckDB's CSV reader raised: its message starts ``CSV Error on Line``. A
failed cast elsewhere is also a ``ConversionException``, and passes through.
"""

import functools
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager

import duckdb
import pyarrow as pa

from sdmxlib.model.errors import SdmxParseError

#: What DuckDB's CSV reader puts in every error it raises, and no other part does.
_CSV_ERROR = "CSV Error"

#: The catalog every scanned file's relation lives in (`sdmxlib.local._scan`).
#: Here rather than there so the query layer can tell a scan from a table
#: without importing `sdmxlib.local`.
SCANS = "sdmx_scans"


def type_mismatch(structure: str, exc: BaseException, *, raw_rows: bool = False) -> ValueError:
    """The error for a value that does not fit its component's type.

    Args:
        structure: The data structure's id.
        exc: The library's error, for the message.
        raw_rows: Whether to name `.stream()` as the way to the untyped rows,
            which holds for a query's cast and not for a CSV read, whose
            columns DuckDB types as it reads.

    Returns:
        The ``ValueError`` to raise from `exc`.
    """
    msg = (
        f"an observation column does not fit its component's type in {structure}: {exc}. "
        "Results are typed from the DSD."
    )
    if raw_rows:
        msg += " .stream() reads the raw rows;"
    msg += (
        " an observation query's .validate(), or sdmxlib.polars.validation_report(dsd, frame), "
        "names every value that does not fit."
    )
    return ValueError(msg)


def _converted(exc: Exception, *, structure: str, any_read_error: bool) -> Exception | None:
    """The error sdmxlib raises for DuckDB's `exc`, or `None` when `exc` is not a CSV read's.

    A stream's error reaches Python through pyarrow as an ``OSError`` carrying
    DuckDB's message, so the kind is read off the message's prefix too.
    """
    text = str(exc)
    if not any_read_error and _CSV_ERROR not in text:
        return None
    if isinstance(exc, duckdb.ConversionException) or text.startswith("Conversion Error"):
        return type_mismatch(structure, exc)
    if isinstance(exc, (duckdb.InvalidInputException, duckdb.BinderException)) or text.startswith(
        "Invalid Input Error"
    ):
        return SdmxParseError(f"the SDMX-CSV data could not be read: {exc}")
    return None


@contextmanager
def reading_csv(*, structure: str) -> Generator[None]:
    """Convert what the one ``read_csv`` statement inside raises.

    Args:
        structure: The data structure's id, for a type mismatch.

    Yields:
        Nothing; the statement runs in the block.

    Raises:
        SdmxParseError: The file is not readable SDMX-CSV.
        ValueError: A value does not fit its component's type.
    """
    try:
        yield
    except duckdb.Error as exc:
        err = _converted(exc, structure=structure, any_read_error=True)
        if err is None:
            raise
        raise err from exc


def _structure_of(query: object) -> str:
    """The data structure id a query reads with, for the message."""
    root = getattr(getattr(query, "_query", query), "root", None)
    dsd = getattr(root, "dsd", None)
    found: object = getattr(dsd, "id", None)
    return found if isinstance(found, str) else "its data structure"


def csv_errors[**P, T](method: Callable[P, T]) -> Callable[P, T]:
    """Convert the CSV read errors a query terminal's statement raises.

    Args:
        method: A query method whose first argument is the query.

    Returns:
        The method, raising `SdmxParseError` or the type ``ValueError`` in
        place of DuckDB's CSV reader errors.
    """

    @functools.wraps(method)
    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return method(*args, **kwargs)
        except duckdb.Error as exc:
            err = _converted(exc, structure=_structure_of(args[0]), any_read_error=False)
            if err is None:
                raise
            raise err from exc

    return run


def reads_scan(query: object) -> bool:
    """Whether `query` reads a scanned file, whose rows DuckDB parses as the query runs."""
    root = getattr(getattr(query, "_query", query), "root", None)
    binding = getattr(root, "binding", None)
    return getattr(binding, "database_role", None) == SCANS


def converting(reader: pa.RecordBatchReader, *, query: object) -> pa.RecordBatchReader:
    """`reader`, raising sdmxlib's errors for a CSV read failure while it is drained.

    A stream runs its statement as it is read, after the terminal returned,
    so the conversion goes with the batches.

    Args:
        reader: The stream DuckDB returned.
        query: The query it reads, for the structure's id.

    Returns:
        A reader over the same batches.
    """

    def batches() -> Iterator[pa.RecordBatch]:
        try:
            yield from reader
        except (duckdb.Error, OSError) as exc:
            err = _converted(exc, structure=_structure_of(query), any_read_error=False)
            if err is None:
                raise
            raise err from exc

    return pa.RecordBatchReader.from_batches(reader.schema, batches())
