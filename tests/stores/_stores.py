"""Object-store emulators in containers, and an upload into each (GH-180).

Each emulator runs from an image pinned by digest, on a free local port, for
the session. Uploading is the publish job's work, which sdmxlib leaves to the
deployment, so the uploads here are plain HTTP: anonymous on the S3 server and
the GCS emulator, Azure's SharedKey signature on Azurite, written out so the
suite needs no Azure SDK.
"""

import base64
import hashlib
import hmac
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

import httpx2
from attrs import frozen

import sdmxlib as sl
from sdmxlib.bundle import read_manifest

#: Azurite's well-known development account, the same in every install.
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="

#: The bucket or container every test writes under.
BUCKET = "bundles"


@frozen
class Emulator:
    """One store's emulator: its image, the port it serves, and how it starts.

    ``{host}`` in `args` becomes the ``127.0.0.1:<port>`` it is reached at:
    the GCS emulator routes a path-style request by its ``Host`` header.
    """

    name: str
    image: str
    port: int
    args: tuple[str, ...]


EMULATORS = (
    Emulator(
        "azure",
        "mcr.microsoft.com/azure-storage/azurite@sha256:830430c1da1a2d537e08f3e6764dd1f5ae00cf0346bcaf625b968ec3f0971fd5",
        10000,
        # The image's own command, which serves on every interface inside the
        # container; `start` publishes the port on 127.0.0.1 alone.
        (),
    ),
    Emulator(
        "s3",
        "chrislusf/seaweedfs@sha256:c2658820e763294a9e9de7b36c81e59b879e3459148351aee4e31a126ca64860",
        8333,
        ("server", "-s3", "-s3.port=8333"),
    ),
    Emulator(
        "gcs",
        "fsouza/fake-gcs-server@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef",
        4443,
        ("-scheme", "http", "-port", "4443", "-public-host", "{host}"),
    ),
)


def runtime_missing() -> str | None:
    """Why no container can run here, or None when one can."""
    if shutil.which("docker") is None:
        return "no docker CLI on PATH"
    if _docker("info", check=False).returncode != 0:
        return "docker is installed but its daemon is not running (start it, e.g. `colima start`)"
    return None


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the docker CLI, found on PATH, with `args`: the tier's one subprocess."""
    docker = shutil.which("docker")
    if docker is None:
        msg = "no docker CLI on PATH"
        raise FileNotFoundError(msg)
    # The docker CLI from shutil.which; arguments are the module's pinned images and docker's own output.
    return subprocess.run([docker, *args], capture_output=True, text=True, check=check)  # noqa: S603


@frozen
class Running:
    """An emulator started for the session."""

    emulator: Emulator
    container: str
    host_port: int

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.host_port}"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def start(emulator: Emulator) -> Running:
    """Run `emulator` on a free local port and wait until it answers HTTP."""
    port = _free_port()
    args = [arg.replace("{host}", f"127.0.0.1:{port}") for arg in emulator.args]
    container = _docker("run", "-d", "--rm", "-p", f"127.0.0.1:{port}:{emulator.port}", emulator.image, *args)
    running = Running(emulator, container.stdout.strip(), port)
    deadline = time.monotonic() + 120
    while True:
        try:
            httpx2.get(running.base, timeout=2)
        except httpx2.TransportError:
            if time.monotonic() > deadline:
                stop(running)
                raise
            time.sleep(0.5)
        else:
            return running


def stop(running: Running) -> None:
    """Remove the emulator's container."""
    _docker("rm", "-f", running.container, check=False)


def secret(running: Running) -> sl.StoreSecret:
    """The DuckDB secret that reads `running`'s store."""
    endpoint = f"127.0.0.1:{running.host_port}"
    match running.emulator.name:
        case "azure":
            connection = (
                f"DefaultEndpointsProtocol=http;AccountName={AZURITE_ACCOUNT};AccountKey={AZURITE_KEY};"
                f"BlobEndpoint={running.base}/{AZURITE_ACCOUNT};"
            )
            return sl.StoreSecret("azure", options={"connection_string": connection})
        case "s3":
            options = {"key_id": "any", "secret": "any", "endpoint": endpoint, "url_style": "path", "use_ssl": False}
            return sl.StoreSecret("s3", options={**options, "region": "us-east-1"})
        case _:
            options = {"key_id": "any", "secret": "any", "endpoint": endpoint, "url_style": "path", "use_ssl": False}
            return sl.StoreSecret("gcs", options=options)


def root(running: Running) -> str:
    """The URI of the bucket every test writes under, ending in ``/``."""
    scheme = {"azure": "az", "s3": "s3", "gcs": "gs"}[running.emulator.name]
    return f"{scheme}://{BUCKET}/"


def uploader(running: Running, client: httpx2.Client) -> Callable[[str, bytes], None]:
    """A function putting bytes at a key under `BUCKET` through `client`, with the bucket made first."""
    match running.emulator.name:
        case "azure":
            _azure(client, "PUT", f"/{AZURITE_ACCOUNT}/{BUCKET}", b"", query={"restype": "container"})

            def put_azure(key: str, body: bytes) -> None:
                path = f"/{AZURITE_ACCOUNT}/{BUCKET}/{key}"
                _azure(client, "PUT", path, body, headers={"x-ms-blob-type": "BlockBlob"})

            return put_azure
        case "s3":
            client.put(f"/{BUCKET}").raise_for_status()

            def put_s3(key: str, body: bytes) -> None:
                client.put(f"/{BUCKET}/{key}", content=body).raise_for_status()

            return put_s3
        case _:
            client.post("/storage/v1/b", json={"name": BUCKET}).raise_for_status()

            def put_gcs(key: str, body: bytes) -> None:
                params = {"uploadType": "media", "name": key}
                client.post(f"/upload/storage/v1/b/{BUCKET}/o", params=params, content=body).raise_for_status()

            return put_gcs


def _azure(
    client: httpx2.Client,
    method: str,
    path: str,
    body: bytes,
    *,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """One request to Azurite, signed with Azure's SharedKey scheme."""
    md5 = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode() if body else ""
    ms = {"x-ms-date": format_datetime(datetime.now(UTC), usegmt=True), "x-ms-version": "2021-08-06", **(headers or {})}
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(ms.items()))
    canonical_resource = f"/{AZURITE_ACCOUNT}{path}" + "".join(f"\n{k}:{v}" for k, v in sorted((query or {}).items()))
    to_sign = (
        "\n".join([method, "", "", str(len(body)) if body else "", md5, "", "", "", "", "", "", ""])
        + f"\n{canonical_headers}{canonical_resource}"
    )
    signature = base64.b64encode(
        hmac.new(base64.b64decode(AZURITE_KEY), to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    sent = {**ms, "Authorization": f"SharedKey {AZURITE_ACCOUNT}:{signature}"}
    if md5:
        sent["Content-MD5"] = md5
    client.request(method, path, params=query, content=body, headers=sent).raise_for_status()


def upload_publication(put: Callable[[str, bytes], None], publication: Path, *, bundle: str) -> None:
    """Upload a publication under `bundle` as the publish job does: data files, the publication, the manifest last."""
    manifest = read_manifest(publication)
    data = publication / manifest.data_path
    for entry in sorted(manifest.files, key=lambda e: e.root != "data"):
        if entry.root == "data":
            put(f"{bundle}/data/{entry.path}", (data / entry.path).read_bytes())
        else:
            put(f"{bundle}/{publication.name}/{entry.path}", (publication / entry.path).read_bytes())
    put(f"{bundle}/{publication.name}/bundle.manifest.json", (publication / "bundle.manifest.json").read_bytes())
