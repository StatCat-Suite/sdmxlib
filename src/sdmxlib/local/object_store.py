"""Object storage a sealed edition's data files are read from (GH-180).

A split deployment holds an edition's catalog files on local disk and reads
its data files from object storage. The edition records no location: the
manifest's ``data_path`` is relative, and the deployment names the remote
edition root, which `data_uri` joins it to. DuckDB reads every store, so
sdmxlib holds no blob client: the scheme picks the extension (`extension_for`)
and the credentials are DuckDB secrets (`StoreSecret`), created on the
`EditionEngine` every edition in the process shares.
"""

import posixpath
import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

import attrs
import duckdb
from attrs import field, frozen

from sdmxlib._duckdb import first

#: The DuckDB extension that reads each URI scheme. ``file`` needs none: it is
#: the local filesystem by URI, for a mounted store or a test.
EXTENSIONS: Mapping[str, str | None] = {
    "s3": "httpfs",
    "s3a": "httpfs",
    "s3n": "httpfs",
    "r2": "httpfs",
    "gs": "httpfs",
    "gcs": "httpfs",
    "http": "httpfs",
    "https": "httpfs",
    "az": "azure",
    "azure": "azure",
    "abfss": "azure",
    "file": None,
}

#: The DuckDB extension that defines each secret type.
SECRET_EXTENSIONS: Mapping[str, str] = {
    "s3": "httpfs",
    "r2": "httpfs",
    "gcs": "httpfs",
    "http": "httpfs",
    "azure": "azure",
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _secret_name(_instance: object, _attribute: attrs.Attribute[str | None], value: str | None) -> None:
    if value is not None and not _IDENTIFIER.match(value):
        msg = f"StoreSecret name {value!r} is not a DuckDB secret name: letters, digits and _"
        raise ValueError(msg)


#: A secret option's value: a string is quoted, a bool or int written bare.
type OptionValue = str | bool | int


def _no_options() -> Mapping[str, OptionValue]:
    return {}


def _option_names(
    _instance: object, _attribute: attrs.Attribute[Mapping[str, OptionValue]], value: Mapping[str, OptionValue]
) -> None:
    for name in value:
        if not _IDENTIFIER.match(name):
            msg = f"StoreSecret option {name!r} is not a DuckDB secret option name: letters, digits and _"
            raise ValueError(msg)


def _secret_type(_instance: object, _attribute: attrs.Attribute[str], value: str) -> None:
    if value not in SECRET_EXTENSIONS:
        msg = f"StoreSecret type {value!r} is not one of {', '.join(sorted(SECRET_EXTENSIONS))}"
        raise ValueError(msg)


@frozen
class StoreSecret:
    """A DuckDB secret: the credentials DuckDB reads one object store with.

    Rendered as DuckDB's ``CREATE SECRET`` and created on a `EditionEngine`,
    so every edition the engine holds reads with it. The fields are DuckDB's
    own; its documentation lists each type's providers and options.

    Example:
        A pod on Azure reading with its workload identity, and a bucket on an
        S3-compatible server:

            sl.StoreSecret("azure", provider="credential_chain", options={"account_name": "acct"})
            sl.StoreSecret(
                "s3",
                options={"key_id": "…", "secret": "…", "endpoint": "minio:9000", "url_style": "path", "use_ssl": False},
            )

    Attributes:
        type: The secret type: ``s3``, ``r2``, ``gcs``, ``http`` or ``azure``.
        provider: How DuckDB finds the credentials, such as ``config`` or
            ``credential_chain``; DuckDB's default for the type when None.
        options: The type's options, by name, such as ``account_name`` or
            ``endpoint``. A string is quoted; a bool or int is written bare.
        scope: The URI prefix the secret applies to, for two accounts of one
            type; every URI of the type when None.
        name: The secret's name; ``sdmxlib_<type>`` when None, so a second
            secret of one type needs a name of its own.
    """

    type: str = field(validator=_secret_type)
    provider: str | None = None
    options: Mapping[str, OptionValue] = field(factory=_no_options, validator=_option_names)
    scope: str | None = None
    name: str | None = field(default=None, validator=_secret_name)

    def statement(self) -> str:
        """This secret's ``CREATE OR REPLACE SECRET`` statement."""
        clauses = [f"TYPE {self.type}"]
        if self.provider is not None:
            clauses.append(f"PROVIDER {sql_literal(self.provider)}")
        clauses += [f"{name.upper()} {_value(option=value)}" for name, value in self.options.items()]
        if self.scope is not None:
            clauses.append(f"SCOPE {sql_literal(self.scope)}")
        return f"CREATE OR REPLACE SECRET {self.name or f'sdmxlib_{self.type}'} ({', '.join(clauses)})"


def sql_literal(value: str) -> str:
    """`value` as a SQL string literal: single-quoted, each quote inside doubled."""
    return "'" + value.replace("'", "''") + "'"


def _value(*, option: OptionValue) -> str:
    if isinstance(option, bool):
        return "true" if option else "false"
    if isinstance(option, int):
        return str(option)
    return sql_literal(option)


def scheme_of(uri: str) -> str:
    """The scheme of `uri`, lowercased.

    Raises:
        ValueError: If `uri` has no scheme, or one no DuckDB extension reads.
    """
    scheme = urlsplit(uri).scheme.lower()
    if scheme not in EXTENSIONS:
        known = ", ".join(f"{s}://" for s in sorted(EXTENSIONS))
        if not scheme:
            msg = f"remote {uri!r} is not a URI; give the store's scheme, one of {known}"
        else:
            msg = f"remote {uri!r} has the scheme {scheme}://, which DuckDB does not read; use one of {known}"
        raise ValueError(msg)
    return scheme


def extension_for(uri: str) -> str | None:
    """The DuckDB extension that reads `uri`, or None for a ``file://`` URI.

    Raises:
        ValueError: As `scheme_of`.
    """
    return EXTENSIONS[scheme_of(uri)]


def data_uri(remote: str, data_path: str) -> str:
    """Where the data files of the edition at `remote` live: `data_path` resolved against it.

    `remote` names the edition directory, with or without a trailing
    ``/``; `data_path` is the manifest's, relative to it. The result ends in
    ``/``, as DuckLake's ``DATA_PATH`` does.

    Example:
        ``data_uri("abfss://c@acct.dfs.core.windows.net/bundles/census/019a/", "../data/")``
        is ``"abfss://c@acct.dfs.core.windows.net/bundles/census/data/"``.

    Raises:
        ValueError: If `remote` is not a URI DuckDB reads, `data_path` is
            absolute, or it climbs above the root of `remote`'s path.
    """
    scheme_of(remote)
    if data_path.startswith("/") or urlsplit(data_path).scheme:
        msg = f"data_path {data_path!r} is not relative to the edition"
        raise ValueError(msg)
    parts = urlsplit(remote)
    segments = [s for s in parts.path.split("/") if s]
    for step in data_path.split("/"):
        if step in {"", "."}:
            continue
        if step == "..":
            if not segments:
                msg = f"data_path {data_path!r} climbs above the root of {remote!r}"
                raise ValueError(msg)
            segments.pop()
        else:
            segments.append(step)
    path = posixpath.join("/", *segments)
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/") + "/", "", ""))


def load_for(conn: duckdb.DuckDBPyConnection, extension: str | None) -> None:
    """Install and load `extension` on `conn`'s database; nothing for None.

    Loading ``azure`` also selects its curl transport, which reads the
    system's CA edition (``SSL_CERT_FILE``, ``CURL_CA_BUNDLE``) where the
    default transport does not. It is set globally, since every per-thread
    handle reads data (GitLab #81).
    """
    if extension is None:
        return
    conn.execute(f"INSTALL {extension}")
    conn.execute(f"LOAD {extension}")
    if extension == "azure":
        conn.execute("SET GLOBAL azure_transport_option_type = 'curl'")


def sizes(conn: duckdb.DuckDBPyConnection, files: list[str]) -> dict[str, int | None]:
    """The size in bytes of each of `files` where its store holds it, None where it holds none.

    Read through DuckDB's filesystems, so a remote file is one ``HEAD``
    request and none is downloaded. ``read_blob`` takes a glob, which a
    DuckLake file name never contains.

    Args:
        conn: A connection with the stores' extensions and secrets loaded.
        files: Paths or URIs, as DuckLake lists them.

    Raises:
        duckdb.Error: If a store refuses the request for a reason other than
            a missing file, such as an expired credential.
    """
    found: dict[str, int | None] = {}
    for file in files:
        try:
            row = first(conn.execute("SELECT size FROM read_blob(?)", [file]))
        except duckdb.HTTPException as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            row = None
        except duckdb.IOException as exc:
            if "No files found" not in str(exc):
                raise
            row = None
        found[file] = None if row is None else int(row[0])
    return found
