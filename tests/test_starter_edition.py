"""Starter-edition guarantees.

Two things the free image must keep true:

  1. With the enterprise packages (`signing.py`, `enforce/`) physically removed,
     the app still imports — no enterprise code anywhere, every feature flag off.
     Verified in a subprocess against a stripped copy of the source tree so
     it mirrors the Docker starter build, not just a monkeypatched flag.

  2. The ledger degrades correctly when signing is absent: entries are still
     written and hash-chained, but carry an empty signature, and
     verify_chain treats them as chain-verified (not signature failures).
"""

import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"


def test_starter_tree_imports_without_enterprise_code(tmp_path):
    """Confirm an enterprise-code-free `kyde` tree imports with both flags False.

    Since the split, this repo (`kyde-gateway`) no longer ships signing.py /
    enforce/ at all — they live in kyde-enterprise. We still defensively strip
    them from the copy (tolerating their absence) so the test also holds if run
    in a tree where the enterprise package happens to be present.

    `kyde` is a PEP 420 namespace package, so a bare PYTHONPATH overlay is not
    enough to isolate: the editable install of this repo (a `.pth` that adds
    `src/` to sys.path) would merge any installed `kyde/` into the same
    namespace and could leak enterprise modules. So we run the subprocess with `-S`
    (no site processing → the editable `.pth` is ignored), point PYTHONPATH at
    the copy, and re-add only the dependency site-packages dirs from inside the
    script. `kyde` then resolves solely to the copy, while fastapi/psycopg/etc.
    remain importable.
    """
    tree = tmp_path / "starter"
    shutil.copytree(_SRC, tree)
    (tree / "kyde" / "signing.py").unlink(missing_ok=True)
    shutil.rmtree(tree / "kyde" / "enforce", ignore_errors=True)

    # Dependency dirs only — these hold no real `kyde/` package (it is editable
    # via a .pth that -S skips), so adding them cannot leak the enterprise modules.
    dep_paths = sorted({sysconfig.get_paths()[k] for k in ("purelib", "platlib")})

    script = (
        "import sys\n"
        f"sys.path.extend({dep_paths!r})\n"
        "import importlib.util as u\n"
        "assert u.find_spec('kyde.signing') is None, 'signing should be absent'\n"
        "assert u.find_spec('kyde.enforce') is None, 'enforce should be absent'\n"
        "from kyde import _features as f\n"
        "assert f.HAS_SIGNING is False and f.HAS_ENFORCEMENT is False, f.edition()\n"
        "assert f.edition() == 'starter'\n"
        "import kyde.ledger as l\n"
        "assert l._HAS_SIGNING is False\n"
        "import kyde.server, kyde.dashboard, kyde.commands, kyde.pdf_export\n"
        "print('STARTER_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-S", "-c", script],
        env={"PYTHONPATH": str(tree), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "STARTER_OK" in proc.stdout


def test_ledger_degrades_to_unsigned_when_signing_absent(monkeypatch):
    """When signing is unavailable the ledger writes an empty signature and
    verify_chain reports the entry as chain-valid, not a signature failure."""
    from kyde import ledger

    monkeypatch.setattr(ledger, "_HAS_SIGNING", False)

    entry = ledger.append(
        agent_id="agent:unsigned",
        action_type="chat",
        model="m",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"content": "ok"}}]},
        why_messages=[],
        tool_calls=[],
    )
    assert entry.signature == ""
    # Hash chain is still populated — tamper-evidence is edition-independent.
    assert entry.entry_hash

    valid, errors = ledger.verify_chain(record=False)
    assert valid, errors
    assert not any("Invalid signature" in e for e in errors)
