"""SDMX-ML 2.1 XML namespace constants."""

MES = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
STR = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
COM = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# Namespace dict for xpath (informational; reader uses Clark notation directly)
NS = {"mes": MES, "str": STR, "com": COM}

# Root namespace that identifies a SDMX-ML 2.1 Structure message
STRUCTURE_ROOT_NS = MES
