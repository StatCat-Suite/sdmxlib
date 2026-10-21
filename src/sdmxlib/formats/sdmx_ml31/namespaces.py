"""SDMX-ML 3.1 XML namespace constants."""

MES = "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/message"
STR = "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/structure"
COM = "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/common"
# GenericMetadata payload namespace (separate from str:)
MD = "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/metadata/generic"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# Namespace dict for xpath (informational; reader uses Clark notation directly)
NS = {"mes": MES, "str": STR, "com": COM, "md": MD}

# Root namespace that identifies a SDMX-ML 3.1 Structure message
STRUCTURE_ROOT_NS = MES
