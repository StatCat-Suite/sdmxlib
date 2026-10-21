"""SDMX-ML 3.0 XML namespace constants."""

MES = "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message"
STR = "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/structure"
COM = "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/common"
# GenericMetadata payload namespace (separate from str:)
MD = "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/metadata/generic"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# Namespace dict for xpath (informational; reader uses Clark notation directly)
NS = {"mes": MES, "str": STR, "com": COM, "md": MD}

# Root namespace that identifies a SDMX-ML 3.0 Structure message
STRUCTURE_ROOT_NS = MES
