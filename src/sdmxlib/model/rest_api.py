"""Which generation of the SDMX-REST API a caller is talking to.

One value, in the model layer rather than beside the HTTP client, because it
changes what an *artefact* means on the wire and not merely how a request is
sent: `DataStructure.key_slot_ids` gives a different answer for each, and a key
built for the wrong one is read with every value a slot out of place (#337).

Spelled as the two strings `RestRegistry` and `ProviderInfo` already use, so
there is one vocabulary rather than a second enum to translate into.
"""

from typing import Literal

#: SDMX-REST API generation. `"2.1"` is the REST 1.5 family, `"3.0"` the 2.2.2
#: family — the names `Provider` entries carry.
type ApiVersion = Literal["2.1", "3.0"]
