"""`q.contact` and `q.agency.contacts` — who to ask about an agency's artefacts.

Two things here are unlike every other relation so far.

**The multi-valued fields are stored list columns.** `emails`, `phones`,
`faxes`, `uris` and `x400s` are `TEXT[]`, so they are asked with `has()` — a
containment test against a column — rather than with `==`. That is the same
operator `ancestors` uses and for the same reason.

**A contact has no identity in the information model.** `agency_contact.id` is
a sequence, so it is the key because nothing else separates two contacts of one
agency, and `ids()` on this root is close to meaningless. `agency.contacts` is
the way in; the root exists for consistency and for `count()`.

Also recorded: `DataProvider` declares contacts and they are dropped on write.
There is no `data_provider_contact` table and `put_data_provider_scheme` never
reads the field, so only an agency's contacts are queryable at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.organisation import Agency, AgencyScheme, Contact, DataProvider, DataProviderScheme
from sdmxlib.query.accessors import q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """One agency with two contacts, one with none, and a provider with one.

    The contactless agency is what a projection needs to show the difference,
    and the provider's contact is the dropped case.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            AgencyScheme(
                id="AGENCIES",
                maintainer=Agency(id="SDMX"),
                name={"en": "Agencies"},
                agencies=[
                    Agency(
                        id="ESTAT",
                        name={"en": "Eurostat"},
                        contacts=(
                            Contact(
                                name={"en": "Statistics desk"},
                                department={"en": "Unit D1"},
                                role={"en": "Enquiries"},
                                emails=("stats@example.org", "alt@example.org"),
                                phones=("+32 2 299 1111",),
                            ),
                            Contact(name={"en": "Press office"}, emails=("press@example.org",)),
                        ),
                    ),
                    Agency(id="OECD", name={"en": "OECD"}),
                ],
            )
        )
        registry.add(
            DataProviderScheme(
                id="DATA_PROVIDERS",
                maintainer=Agency(id="SDMX"),
                name={"en": "Providers"},
                providers=[DataProvider(id="ECB", name={"en": "ECB"}, contacts=(Contact(name={"en": "Desk"}),))],
            )
        )
        yield registry


# ── the relation ──────────────────────────────────────────────────────────


def test_contacts_are_a_root(reg: sl.LocalRegistry) -> None:
    """Three: two on the agency, one on the data provider (#309)."""
    assert reg.query(q.contact).count() == 3


def test_an_agency_fans_out_to_its_contacts(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.agency)
        .order_by(q.agency.id)
        .select(agency=q.agency.id, who=q.agency.contacts.name["en"].list())
        .collect()
    )
    rows = dict(zip(frame["agency"].to_list(), frame["who"].to_list(), strict=True))
    assert sorted(rows["ESTAT"]) == ["Press office", "Statistics desk"]
    # An agency with no contacts collects to `[]`, not None (#341).
    assert rows["OECD"] == []


def test_a_contact_reaches_its_agency(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.contact).filter(q.contact.agency.id == "ESTAT").count() == 2
    assert reg.query(q.contact).filter(q.contact.agency.id == "OECD").count() == 0


def test_a_contact_reaches_its_agencys_scheme(reg: sl.LocalRegistry) -> None:
    """Two hops — the contact's agency, then the scheme that declares it."""
    assert reg.query(q.contact).filter(q.contact.agency.scheme.id == "AGENCIES").count() == 2


def test_the_localised_fields_are_maps(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.contact).filter(q.contact.department["en"] == "Unit D1")
    assert found.select(n=q.contact.name["en"]).collect()["n"].to_list() == ["Statistics desk"]


# ── the list columns ──────────────────────────────────────────────────────


def test_a_list_column_is_asked_with_has(reg: sl.LocalRegistry) -> None:
    """`emails` holds many values per row, so membership is the question. `==`
    against a list would compare a scalar to an array and match nothing."""
    found = reg.query(q.contact).filter(q.contact.emails.has("press@example.org"))
    assert found.select(n=q.contact.name["en"]).collect()["n"].to_list() == ["Press office"]


def test_has_finds_a_value_that_is_not_the_first(reg: sl.LocalRegistry) -> None:
    """A containment test that only ever checked element zero would pass the
    previous test and fail here."""
    found = reg.query(q.contact).filter(q.contact.emails.has("alt@example.org"))
    assert found.select(n=q.contact.name["en"]).collect()["n"].to_list() == ["Statistics desk"]


def test_the_whole_list_projects(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.contact)
        .order_by(q.contact.position)
        .select(name=q.contact.name["en"], emails=q.contact.emails)
        .collect()
    )
    # The provider's contact is `ordinal` 0 within *its* owner, so it sorts
    # alongside the agency's first — position is per-organisation, not global.
    assert frame.to_dicts() == [
        {"name": "Statistics desk", "emails": ["stats@example.org", "alt@example.org"]},
        {"name": "Desk", "emails": []},
        {"name": "Press office", "emails": ["press@example.org"]},
    ]


def test_an_agency_is_found_through_a_contacts_list(reg: sl.LocalRegistry) -> None:
    """Two hops and a containment test — "whose desk is this address"."""
    assert reg.query(q.agency).filter(q.agency.contacts.emails.has("stats@example.org")).ids() == ["ESTAT"]
    assert reg.query(q.agency).filter(q.agency.contacts.emails.has("nobody@example.org")).ids() == []


def test_an_empty_list_matches_nothing(reg: sl.LocalRegistry) -> None:
    """The press contact has no phone. `has` on an empty list must be false
    rather than null-propagating into a match."""
    found = reg.query(q.contact).filter(q.contact.phones.has("+32 2 299 1111"))
    assert found.select(n=q.contact.name["en"]).collect()["n"].to_list() == ["Statistics desk"]


# ── what is not here ──────────────────────────────────────────────────────


def test_a_data_providers_contacts_are_persisted_too(reg: sl.LocalRegistry) -> None:
    """The inverse of what this asserted until #309.

    `agency_contact` was keyed by `agency_urn` and there was no
    `data_provider_contact` table, so `put_data_provider_scheme` dropped the
    field — a model field with storage behind it for one organisation and not
    the other, though `Agency` and `DataProvider` are both `Organisation` in
    the IM and carry the identical `contacts`. One `organisation_contact`
    table serves both now.

    The old assertion is kept in shape rather than deleted: it counted the
    contacts and found the provider's missing, so counting is still what
    proves the fix.
    """
    assert reg.query(q.contact).count() == 3, "two agency contacts and the provider's"
    assert hasattr(q.data_provider, "contacts")


def test_each_contact_resolves_to_its_own_owner_and_not_the_other(reg: sl.LocalRegistry) -> None:
    """The polymorphic owner, and why both edges are `optional`.

    `owner_urn` matches an agency *or* a provider, never both. With inner
    joins, selecting both hops at once returns **nothing** — measured, before
    the edges were marked optional — and each hop alone would silently drop
    every contact of the other kind.
    """
    rows = (
        reg.query(q.contact)
        .select(q.contact.name["en"], ag=q.contact.agency.id, dp=q.contact.data_provider.id)
        .collect()
        .to_dicts()
    )

    assert len(rows) == 3, "no row is lost to a join that cannot match"
    for row in rows:
        assert (row["ag"] is None) != (row["dp"] is None), f"exactly one owner: {row}"


def test_the_key_is_a_storage_surrogate(reg: sl.LocalRegistry) -> None:
    """`agency_contact.id` is a sequence, not an SDMX identifier — a contact
    has none. Worth pinning because it makes `ids()` on this root meaningless,
    which is a surprise unless it is written down."""
    assert type(q.contact).__key__ == ("id",)
    assert all(value.isdigit() for value in reg.query(q.contact).ids())
