"""
Witness – Agent Behavioral Ledger Proxy
========================================
Drop-in OpenAI-compatible proxy that signs and logs every agent interaction
into a tamper-evident, append-only behavioral ledger.

Usage:
    kyde serve             # Start proxy on :8000
    kyde keygen            # Generate signing keypair
    kyde ledger list       # Show all ledger entries
    kyde ledger verify     # Verify ledger integrity
    kyde ledger show <id>  # Show single entry detail

Agent config (one line change):
    OPENAI_BASE_URL=http://localhost:8000/v1
    OPENAI_API_KEY=<your-real-key>      # passed through transparently
"""

import sys
from .commands import run_cli

if __name__ == "__main__":
    run_cli(sys.argv[1:])
