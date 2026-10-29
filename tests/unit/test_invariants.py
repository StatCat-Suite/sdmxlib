"""Every class with whole-artefact rules runs them as it is built (#492).

The rules are registered with `_invariants` by `validation`, and a class runs
them only if its `__attrs_post_init__` calls `enforce` — a class that forgot
would build anything, silently, which is what `validate()` binding only where
it was called used to allow. So each registered class is rebuilt here with a
rule that always fails swapped in, and must raise.
"""

from collections.abc import Callable
from typing import TypeIs

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model import _invariants
from sdmxlib.model.errors import ValidationError, ValidationIssue
from tests.support import maximal
from tests.support.unchecked import unchecked

pytestmark = pytest.mark.unit


def _registered() -> list[type]:
    return sorted(_invariants._CHECKS, key=lambda cls: cls.__name__)


#: `attrs.evolve` for an artefact whose class the checker cannot see — the
#: spelling `model.version_rewrite` uses: it rebuilds, running every rule.
_rebuild: Callable[..., object] = attrs.evolve


def _is_attrs(value: object) -> TypeIs[attrs.AttrsInstance]:
    return attrs.has(type(value))


def _built_of(cls: type) -> list[attrs.AttrsInstance]:
    found = [a for a in maximal.message().all_artefacts() if isinstance(a, cls) and _is_attrs(a)]
    if found or cls is not sl.AttachmentConstraint:
        return found
    # The one registered class the maximal fixture does not build.
    flow = sl.Dataflow.urn_for(agency="AG", id="DF", version="1.0")
    attachment = sl.ConstraintAttachment(dataflows=(sl.Ref[sl.Dataflow].unresolved(flow),))
    return [sl.AttachmentConstraint(id="AC", maintainer="AG", name={"en": "ac"}, attachment=attachment)]


def test_the_registry_holds_every_class_with_rules():
    """Exact: a class registered or lost moves it."""
    assert [cls.__name__ for cls in _registered()] == [
        "AgencyScheme",
        "AttachmentConstraint",
        "CategoryScheme",
        "Codelist",
        "ConceptScheme",
        "DataConstraint",
        "DataConsumerScheme",
        "DataProviderScheme",
        "DataStructure",
        "Hierarchy",
        "MetadataProviderScheme",
        "MetadataStructure",
        "OrganisationUnitScheme",
        "StructureMap",
    ]


def _fail_every_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every registered class's rules by one that always fails — after building what to rebuild."""

    def always(_artefact: object, prefix: str, issues: list[ValidationIssue]) -> None:
        issues.append(ValidationIssue(path=f"{prefix}.sabotage", message="always"))

    for cls in _registered():
        monkeypatch.setitem(_invariants._CHECKS, cls, always)


@pytest.mark.parametrize("cls", _registered(), ids=lambda cls: cls.__name__)
def test_each_registered_class_enforces_its_rules_as_it_is_built(cls: type, monkeypatch: pytest.MonkeyPatch) -> None:
    built = _built_of(cls)
    assert built, f"the maximal fixture builds no {cls.__name__}, so this checks nothing"
    _fail_every_rule(monkeypatch)
    for artefact in built:
        with pytest.raises(ValidationError, match=r"\.sabotage — always"):
            _rebuild(artefact)


def test_disabled_validators_skip_the_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """How a test builds an artefact a rule refuses, to check what refuses it downstream."""
    (codelist, *_) = _built_of(_invariants_class("Codelist"))
    _fail_every_rule(monkeypatch)
    with unchecked():
        _rebuild(codelist)


def _invariants_class(name: str) -> type:
    return next(cls for cls in _registered() if cls.__name__ == name)
