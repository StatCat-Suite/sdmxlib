"""Rewriting versions SDMX 3.x cannot hold, at ingest.

`1.03` and `1.2.3.4` are valid SDMX 2.1 and have no SDMX 3.x form, so a
registry refuses them. `add_all` takes a rewrite to ingest them anyway:

    reg.add_all(msg.all_artefacts(), rewrite_version=sl.versions.strip_leading_zeros)
    reg.add_all(msg.all_artefacts(), rewrite_version={"1.2.3.4": "1.2.4"})

It returns one `RewrittenVersion` per artefact whose version changed. See
`sdmxlib.model.version_rewrite` for the rules.
"""

from sdmxlib.model.version_rewrite import RewrittenVersion, VersionRewrite, strip_leading_zeros

__all__ = ["RewrittenVersion", "VersionRewrite", "strip_leading_zeros"]
