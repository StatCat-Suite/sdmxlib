"""A 2.1 HierarchicalCodelist written in the alias form reads whole (#434).

2.1's `HierarchicalCodeType` names its code either with a full `<Code><Ref>`,
or with `<CodelistAliasRef>` + `<CodeID><Ref id=…/>`: the code's id alone,
and an alias standing for one of the HCL's `<IncludedCodelist alias=…>`.
OECD writes every 2.1 HCL the second way. The reader took the code from the
`Ref` only when it carried agency and parent too, so all 8,297 of OECD's
codes came back as none — and every writer then emitted a node with no
code, which no format accepts.
"""

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, HierarchicalCodelist, Hierarchy
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_OECD_ALIAS_FORM = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
 xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
 xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
 <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test><mes:Prepared>2026-01-01T00:00:00</mes:Prepared>
  <mes:Sender id="X"/></mes:Header>
 <mes:Structures><str:HierarchicalCodelists>
  <str:HierarchicalCodelist id="HCL_AGE" agencyID="OECD.CFE.EDS" version="1.0" isFinal="true">
   <com:Name xml:lang="en">HCL age</com:Name>
   <str:IncludedCodelist alias="CL_AGE@OECD@11">
    <Ref id="CL_AGE" version="1.1" agencyID="OECD" package="codelist" class="Codelist"/>
   </str:IncludedCodelist>
   <str:Hierarchy id="H" leveled="false"><com:Name xml:lang="en">H</com:Name>
    <str:HierarchicalCode id="_T">
     <str:CodelistAliasRef>CL_AGE@OECD@11</str:CodelistAliasRef>
     <str:CodeID><Ref id="_T"/></str:CodeID>
     <str:HierarchicalCode id="Y_LT15">
      <str:CodelistAliasRef>CL_AGE@OECD@11</str:CodelistAliasRef>
      <str:CodeID><Ref id="Y_LT15"/></str:CodeID>
     </str:HierarchicalCode>
    </str:HierarchicalCode>
   </str:Hierarchy>
  </str:HierarchicalCodelist>
 </str:HierarchicalCodelists></mes:Structures>
</mes:Structure>"""

_CL_AGE = "urn:sdmx:org.sdmx.infomodel.codelist.Code=OECD:CL_AGE(1.1)"


def _read() -> StructureMessage:
    message = parse(_OECD_ALIAS_FORM)
    assert isinstance(message, StructureMessage)
    return message


def test_the_alias_resolves_each_code_against_its_included_codelist() -> None:
    (hierarchy,) = _read().hierarchies
    root = hierarchy.tree["_T"]
    assert root.code is not None
    assert str(root.code.urn) == f"{_CL_AGE}._T"
    (child,) = root.children
    assert child.code is not None
    assert str(child.code.urn) == f"{_CL_AGE}.Y_LT15"


def test_the_container_keeps_its_included_codelists_and_finality() -> None:
    (hcl,) = _read().hierarchical_codelists
    # `isFinal="true"` on a `1.0`, which SDMX 3.0 reads as a draft (#420): the
    # 2.1 flag is kept beside the derived answer, not folded into it.
    assert hcl.legacy_is_final is True
    assert hcl.is_final is False
    assert [str(ref.urn) for ref in hcl.included_codelists] == [
        "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=OECD:CL_AGE(1.1)"
    ]


def test_it_writes_back_as_valid_2_1_and_reads_back_equal() -> None:
    message = _read()
    written = serialize(message, version="2.1")
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    again = parse(written)
    assert isinstance(again, StructureMessage)
    assert again.hierarchies == message.hierarchies
    assert again.hierarchical_codelists == message.hierarchical_codelists


def test_the_store_keeps_the_included_codelists() -> None:
    (hcl,) = _read().hierarchical_codelists
    with LocalRegistry(":memory:") as registry:
        registry.add(hcl)
        back = registry.get(hcl.urn)
    assert isinstance(back, HierarchicalCodelist)
    assert back.included_codelists == hcl.included_codelists


def test_a_node_without_a_code_is_refused_as_it_is_built() -> None:
    """Every format requires the code, so it was refused at the write, then at
    `add` (#419), and now as the hierarchy is built (#492)."""
    codelist = Codelist(id="CL", maintainer="AG", name={"en": "c"}, codes=[Code(id="A", name={"en": "a"})])
    with pytest.raises(ValidationError, match=r"codes\[ORPHAN\]\.code"):
        Hierarchy(
            id="H",
            maintainer="AG",
            name={"en": "h"},
            tree=[HierarchicalCode(id="A", code=codelist.ref_to("A")), HierarchicalCode(id="ORPHAN")],
        )
