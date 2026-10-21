"""Tests for sdmxlib.model.dataflow.Dataflow."""

import pytest

from sdmxlib.model.dataflow import Dataflow

pytestmark = pytest.mark.unit


def test_dataflow_dimension_constraint_default_empty():
    """A Dataflow without a 3.1-style DimensionConstraint accepts the full
    DSD dimensionality. Empty tuple is the absence-of-constraint sentinel."""
    df = Dataflow(name={"en": "DF_X"}, id="DF_X", maintainer="T")
    assert df.dimension_constraint == ()


def test_dataflow_dimension_constraint_carries_dimension_ids():
    """The field is an ordered tuple of dimension ids the Dataflow commits
    to. The XSD docs forbid listing the time dimension; that constraint is
    a documentation note, not a runtime check."""
    df = Dataflow(
        name={"en": "DF_X"},
        id="DF_X",
        maintainer="T",
        dimension_constraint=("FREQ", "REF_AREA"),
    )
    assert df.dimension_constraint == ("FREQ", "REF_AREA")


def test_dataflow_dimension_constraint_is_immutable_tuple():
    df = Dataflow(name={"en": "DF_X"}, id="DF_X", maintainer="T", dimension_constraint=("FREQ",))
    # tuple type means callers can't mutate the field in-place
    assert isinstance(df.dimension_constraint, tuple)
