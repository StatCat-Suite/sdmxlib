"""Record what a live FMR makes of our documents, for the replay test to check (#457).

Needs a Fusion Metadata Registry to write to — a local one, never a public
registry (see `README.md`). Per document it starts from a clean slate, loads
`documents.setup`, submits the adjusted document, then records:

- ``sent.xml``, what was submitted, and ``accepted.json``, the URNs FMR's
  submission report accepted. The replay test, not this script, compares them
  with what was sent: FMR converts 2.1 containers into 3.x artefacts, so the
  counts differ legitimately -- and FMR 12.4.2 has also reported success on a
  submission it silently truncated, so nothing here trusts the report.
- ``necessity.json``, FMR's answer to the document with each adjustment left
  out in turn — the evidence that each one is still needed.
- ``back/``, everything FMR then serves for the test agencies, in SDMX-ML 2.1
  and 3.0 and SDMX-JSON 1.0 and 2.0, with each response's status.

Once per run, ``fmr.json`` records the FMR version and what it makes of
SDMX-ML 3.1, which it neither reads nor serves.
"""

import json
import re
from pathlib import Path

import attrs
import httpx
from lxml import etree

from bench.fmr import documents
from bench.fmr.ledger import Document, Version
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.collections import ArtefactList
from tests.support import maximal
from tests.support.repo import find_root
from tests.support.roundtrip import as_structure

#: Where the recordings live, one directory per FMR version.
RECORDINGS = find_root() / "tests" / "integration" / "fmr"

#: What FMR is asked for, by the name the recordings use.
FORMATS: dict[str, tuple[str, str]] = {
    "ml21": ("application/vnd.sdmx.structure+xml;version=2.1", "xml"),
    "ml30": ("application/vnd.sdmx.structure+xml;version=3.0.0", "xml"),
    "json10": ("application/vnd.sdmx.structure+json;version=1.0.0", "json"),
    "json20": ("application/vnd.sdmx.structure+json;version=2.0.0", "json"),
}
_ML31 = "application/vnd.sdmx.structure+xml;version=3.1.0"


def maintainables(document: bytes) -> int:
    """How many maintainable artefacts our reader finds in `document`."""
    message = as_structure(parse(document))
    return sum(
        1
        for field in attrs.fields(type(message))
        if isinstance(value := getattr(message, field.name), ArtefactList)
        for _ in value
    )


class Fmr:
    """The few calls the recorder makes to one FMR instance."""

    def __init__(self, url: str, auth: tuple[str, str]) -> None:
        self._client = httpx.Client(base_url=url, auth=auth, timeout=300)

    def version(self) -> str:
        """FMR's own version, from its product info."""
        info = self._client.get("/ws/fusion/info/product").raise_for_status().json()
        version = info["Version"]
        assert isinstance(version, str)
        return version

    def submit(self, body: bytes, action: str = "REPLACE") -> httpx.Response:
        """Post structures, as `ACTION` says."""
        headers = {"ACTION": action, "Content-Type": "application/xml"}
        return self._client.post("/ws/secure/sdmxapi/rest/", content=body, headers=headers)

    def fetch(self, agency: str, accept: str) -> httpx.Response:
        """Everything one agency maintains, in one format."""
        return self._client.get(f"/sdmx/v2/structure/*/{agency}/*/*", headers={"Accept": accept})

    def wipe(self, agencies: list[str]) -> None:
        """Delete what the agencies maintain, until nothing is left (deletes can depend on each other)."""
        for _ in range(5):
            served = [self.fetch(agency, FORMATS["ml30"][0]) for agency in agencies]
            served = [r for r in served if r.status_code == httpx.codes.OK]
            if not served:
                return
            for response in served:
                self.submit(response.content, "DELETE")
        msg = f"could not empty FMR of {agencies}"
        raise RuntimeError(msg)


def outcome(response: httpx.Response) -> dict[str, object]:
    """A submission's status, the URNs it accepted, and the first refusal's text."""
    accepted: list[str] = []
    refusals: list[str] = []
    if b"RegistryInterface" in response.content[:600]:
        report = etree.fromstring(response.content)
        for result in report.iter("{*}SubmissionResult"):
            urn = next((u.text or "" for u in result.iter("{*}URN")), "")
            status = next((s.get("status") for s in result.iter("{*}StatusMessage")), None)
            if status == "Success":
                accepted.append(urn)
            else:
                texts = [t.text for t in result.iter("{*}Text") if t.text]
                refusals.append(f"{urn}: {texts[-1] if texts else status}")
    else:
        found = re.search(r"<com:Text>(.*?)</com:Text>", response.text, re.DOTALL)
        refusals.append(found.group(1).strip() if found else response.text[:500])
    return {"status": response.status_code, "accepted": sorted(accepted), "refusal": refusals[0] if refusals else None}


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _dump(path: Path, value: object) -> None:
    _write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def record_case(fmr: Fmr, out: Path, version: Version, document: Document, setup: bytes, agencies: list[str]) -> None:
    """One document: necessity of each adjustment, then the accepted submission and what FMR serves."""
    necessity = []
    for adjustment in documents.adjustments(version, document):
        fmr.wipe(agencies)
        fmr.submit(setup).raise_for_status()
        result = outcome(fmr.submit(documents.adjusted(version, document, without=adjustment)))
        necessity.append({"xpath": adjustment.xpath, **result})
    _dump(out / "necessity.json", necessity)

    fmr.wipe(agencies)
    fmr.submit(setup).raise_for_status()
    sent = documents.adjusted(version, document)
    result = outcome(fmr.submit(sent))
    _write(out / "sent.xml", sent)
    _dump(out / "accepted.json", result)
    if result["refusal"] is not None:
        msg = f"FMR refused {version} {document}: {result['refusal']}"
        raise RuntimeError(msg)
    accepted = result["accepted"]
    assert isinstance(accepted, list)
    print(f"{version} {document}: {len(accepted)} accepted; {maintainables(sent)} read from what was sent")

    statuses: dict[str, int] = {}
    for agency in agencies:
        for name, (accept, extension) in FORMATS.items():
            response = fmr.fetch(agency, accept)
            statuses[f"{agency}-{name}"] = response.status_code
            _write(out / "back" / f"{agency}-{name}.{extension}", response.content)
    _dump(out / "back" / "status.json", statuses)


def record(url: str, auth: tuple[str, str]) -> Path:
    """Record every case against the FMR at `url`; the directory written."""
    fmr = Fmr(url, auth)
    out = RECORDINGS / fmr.version()
    cases = [(v, d) for v in documents.VERSIONS for d in documents.DOCUMENTS]
    sent_raw = [documents.written(v, d) for v, d in cases]
    setup = documents.setup(sent_raw)
    agencies = sorted({str(a) for doc in sent_raw for a in etree.fromstring(doc).xpath("//@agencyID")} - {"SDMX"})
    _write(out / "setup.xml", setup)
    for version, document in cases:
        record_case(fmr, out / f"{version}-{document}", version, document, setup, agencies)

    fmr.wipe(agencies)
    fmr.submit(setup).raise_for_status()
    ml31 = outcome(fmr.submit(ml31_writer.write(maximal.message())))
    # Fetched with something to serve, so a refusal is about 3.1 and not an empty result.
    fmr.submit(documents.adjusted("3.0", "shape")).raise_for_status()
    served = fmr.fetch("AG", _ML31)
    assert fmr.fetch("AG", FORMATS["ml30"][0]).status_code == httpx.codes.OK
    _dump(
        out / "fmr.json",
        {
            "version": fmr.version(),
            "agencies": agencies,
            "ml31": {"submit": ml31, "fetch_status": served.status_code},
        },
    )
    fmr.wipe(agencies)
    return out
