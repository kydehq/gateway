"""Edition seam — the single place that knows which enterprise packages shipped.

The starter image is built by physically removing `signing.py` and the
`enforce/` package before install (see the Dockerfile EDITION build arg).
When they're absent the capability flags go False; every caller routes
through this module, so no other file needs a try/except.

We deliberately distinguish "package physically absent" (starter build →
feature off) from "package present but fails to import" (a broken enterprise
build → must surface loudly). A security product must never fail-open to
"no enforcement" just because a transitive dependency is missing. So we
gate on `find_spec` (does the module exist on disk?) and let any real
import error from a *present* package propagate.

`ledger.py` does NOT use this seam — it guards signing with its own local
try/except to stay the lowest layer and avoid the import cycle
(ledger → _features → enforce → ledger).
"""

import importlib.util

# enterprise: audit signing (Ed25519 + TPM/HSM)
HAS_SIGNING = importlib.util.find_spec("kyde.signing") is not None
if HAS_SIGNING:
    from . import signing  # a present-but-broken module raises here, as it should
else:
    signing = None  # type: ignore[assignment]

# enterprise: enforcement (inline DLP prevention + agent block-list)
HAS_ENFORCEMENT = importlib.util.find_spec("kyde.enforce") is not None
if HAS_ENFORCEMENT:
    from . import enforce
else:
    enforce = None  # type: ignore[assignment]


def edition() -> str:
    """Human-readable edition label for diagnostics and the dashboard."""
    return "enterprise" if (HAS_SIGNING or HAS_ENFORCEMENT) else "starter"
