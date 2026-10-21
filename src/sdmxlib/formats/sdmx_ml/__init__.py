"""Shared SDMX-ML reader/writer helpers — parameterised by namespace URIs.

`sdmxlib.formats.sdmx_ml21` and `sdmxlib.formats.sdmx_ml30` import the
same shapes (Codelist, DataStructure, Dataflow, etc.) and use almost
the same XML scaffolding — only the namespace URIs differ. This module
houses helpers that are byte-identical between the two versions, so
each version's reader/writer pulls them in instead of redefining them.
"""
