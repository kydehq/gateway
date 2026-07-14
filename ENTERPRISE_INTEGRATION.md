# KYDE: ITIL Integration Guide for Enterprise

## Table of Contents
1. [Overview](#overview)
2. [ITIL Alignment](#itil-alignment)
3. [Phase 1 Architecture](#phase-1-architecture)
4. [Deployment](#deployment)
5. [Integration Points](#integration-points)
6. [Monitoring & Alerting](#monitoring--alerting)
7. [Audit & Compliance](#audit--compliance)
8. [Troubleshooting](#troubleshooting)

---

## Overview

**KYDE** is an append-only behavioral ledger for AI agent interactions. It provides cryptographically-signed audit trails and operational metrics that enable your enterprise to maintain governance, compliance, and visibility over LLM-based applications.

Unlike traditional ITIL solutions focused on IT service management, kyde is a **compliance and observability layer** that allows your ITSM tools (ServiceNow, Jira Service Management, etc.) to consume structured data about AI agent behavior—enabling you to:

- **Audit** every tool call, model invocation, and system action
- **Verify** ledger integrity through cryptographic signing (Ed25519 or TPM ECDSA P-256)
- **Monitor** KPIs for SLA compliance
- **Respond** to incidents with complete forensic context
- **Comply** with regulations requiring audit trails (SOC 2, HIPAA, PCI-DSS, etc.)

---

## ITIL Alignment

KYDE supports **ITIL Phase 1**, which covers the minimum technical surface for ITIL compliance:

| ITIL Process | KYDE Component | Details |
|---|---|---|
| **Service Asset & Configuration Management (SACM)** | `/api/configuration` | Real-time snapshot of signing keys, algorithms, upstreams, version |
| **IT Service Continuity Management (ITSCM)** | `/api/metrics` | Uptime, entry throughput, signature success rate, chain integrity |
| **Incident Management (IM)** | `/api/incidents` | Auto-populated from ledger errors; severity levels; structured format |
| **Change Management (CAB)** | Ledger entries | All admin operations (keygen, policy changes) logged with full context |
| **Event Management** | Ledger stream | Real-time agent behavior; tool calls; model invocations |

---

## Phase 1 Architecture

### Core Concepts

**Ledger**: Postgres database with hash-chained entries (JSONB columns for structured fields). Each entry is cryptographically signed and includes:
- `entry_id`: UUID for the action
- `timestamp`: ISO 8601 time
- `agent_id`: Identity of the agent (e.g., `agent:claude-opus`)
- `action_type`: `tool_call`, `chat`, or `admin`
- `model`: LLM model name
- `tool_calls`: Array of function calls made by the LLM
- `why`: Last 2 messages for context (reasoning trace)
- `full_messages`: Complete conversation history (audit only)
- `signature`: Ed25519 or ECDSA P-256 detached signature
- `prev_hash`: Link to previous entry (chain integrity)

**Signing Modes**:
- **Software (Ed25519)**: Fast, suitable for most use cases. Private key in `~/.agent-ledger/signing.key`
- **TPM (ECDSA P-256)**: Hardware-backed, suitable for high-security environments. Private key never leaves the TPM.


---

## Deployment

### 1. Installation

```bash
# Standard installation
pip install kyde-gateway

# With TPM support (for hardware-backed signing)
pip install kyde-gateway[tpm]
```

### 2. Initialize Signing Keys

```bash
# Option A: Software keys (Ed25519)
kyde keygen --type local

# Option B: TPM keys (requires tpm2-pytss and TPM 2.0 device)
kyde keygen --type tpm
```

Both commands will:
- Generate keys
- Store paths in `~/.agent-ledger/`
- Log the keygen operation to the ledger (for audit)
- Display fingerprint (for out-of-band verification)

### 3. Start the Dashboard

```bash
# Default: localhost:8501
kyde dashboard --port 8501
```

Navigate to `http://localhost:8501`, log in, and verify:
- Key configuration (signing mode, algorithm, fingerprint)
- Current ledger entries
- Verification status

### 4. Start the Proxy Server

```bash
# Default: localhost:8000, upstream=openai
kyde serve --port 8000

# Custom upstream (anthropic, gemini, copilot)
export UPSTREAM=anthropic
kyde serve --port 8000
```

Configure your AI agents to point to the proxy:
```bash
export OPENAI_BASE_URL="http://localhost:8000/v1"
export OPENAI_API_KEY="your-api-key"
```

The proxy is **transparent**—agents see no difference. Requests are forwarded, responses returned, and ledger entries appended.

---

## Integration Points

### API Endpoints

All endpoints require session authentication (login cookie).

#### 1. `/api/metrics` — KPI Dashboard

**Purpose**: Feed SLA dashboards, alerting systems, and business intelligence.

```bash
curl -b "session=$TOKEN" http://localhost:8501/api/metrics
```

**Response**:
```json
{
  "total_entries": 1250,
  "entries_per_hour_24h": 52.08,
  "entries_per_hour_1h": 48,
  "signature_success_rate": 0.9992,
  "tool_call_ratio": 0.65,
  "chain_integrity": {
    "valid": true,
    "break_count": 0
  },
  "signing_mode": "TPM",
  "ledger_size_bytes": 8388608,
  "service_start_time": "2026-03-19T09:30:15Z",
  "uptime_seconds": 43200
}
```

**ITSM Integration**:
- Push `signature_success_rate` to monitoring dashboard (alert if < 0.99)
- Track `entries_per_hour_24h` for capacity planning
- `uptime_seconds` feeds SLA reporting
- `chain_integrity.break_count` triggers P1 incident if > 0

---

#### 2. `/api/configuration` — Configuration Management Database (CMDB)

**Purpose**: CMDB sync, version tracking, security posture snapshot.

```bash
curl -b "session=$TOKEN" http://localhost:8501/api/configuration
```

**Response**:
```json
{
  "signing_mode": "tpm",
  "tpm_available": true,
  "algorithm": "ECDSA-P256",
  "public_key_fingerprint": "a3f2b1c9d4e8f7a6",
  "key_paths": {
    "private_key": {
      "path": "/home/user/.agent-ledger/signing.key",
      "exists": false
    },
    "public_key": {
      "path": "/home/user/.agent-ledger/signing.pub",
      "exists": true
    },
    "tpm_key": {
      "path": "/home/user/.agent-ledger/tpm_key.pem",
      "exists": true
    }
  },
  "default_upstream": "anthropic",
  "configured_upstreams": ["openai", "anthropic", "gemini", "copilot"],
  "ledger_backend": "postgres",
  "ledger_entry_count": 1250,
  "service_version": "0.1.0"
}
```

**ITSM Integration**:
- Sync `signing_mode` and `algorithm` to CMDB
- Track key fingerprints for rotation audits
- Monitor `ledger_entry_count` for growth trends
- Alert if `tpm_available` changes unexpectedly

---

#### 3. `/api/incidents` — Incident Feed

**Purpose**: Auto-populated incident stream for incident management.

```bash
curl -b "session=$TOKEN" http://localhost:8501/api/incidents?status=open
```

**Response**:
```json
[
  {
    "id": "inc-a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6",
    "timestamp": "2026-03-19T14:22:33Z",
    "severity": "critical",
    "component": "ledger",
    "description": "Chain break detected: [entry-789abc] signature mismatch at seq 1245",
    "status": "open"
  },
  {
    "id": "inc-p7q8r9s0-t1u2-43v4-w5x6-y7z8a9b0c1d2",
    "timestamp": "2026-03-19T14:18:00Z",
    "severity": "high",
    "component": "ledger",
    "description": "Ledger write failed: database is locked (timeout)",
    "status": "open"
  }
]
```

**Severity Mapping**:
- `critical`: Chain break, cryptographic failure
- `high`: Ledger write failures, TPM unavailability
- `medium`: Signature verification failures
- `low`: Configuration drift

**ITSM Integration**:
- Sync incidents to ServiceNow, Jira, or PagerDuty via webhook
- Auto-create P1 tickets for `critical` and `high` severity
- Deduplication by `id` (prevents duplicates)
- Closed incidents can be marked `status: closed` via ledger UI

---

#### 4. `/api/stats` — Ledger Statistics

**Purpose**: Detailed entry breakdown and summaries.

```bash
curl -b "session=$TOKEN" http://localhost:8501/api/stats
```

Returns aggregated ledger statistics: entries by agent, by action type, by upstream.

---

### CLI for Operational Tasks

#### Verify Ledger Integrity

```bash
kyde ledger verify
```

Output:
```
Verifying ledger chain integrity...
  Entries to verify: 1250
✓ Ledger is intact — all signatures valid, chain unbroken.
```

**Use case**: Post-deployment verification, audit procedures, incident response.

---

#### List Recent Entries

```bash
kyde ledger list
```

Displays last 50 entries in table format (seq, time, agent, action, model, tools).

**Use case**: Operational dashboards, troubleshooting, forensic investigation.

---

#### Show Entry Details

```bash
kyde ledger show <entry_id|seq>
```

Full entry details including:
- Complete message history
- All tool calls with arguments
- Reasoning context (why messages)
- Cryptographic proof (input/output hashes, signature)

**Use case**: Incident investigation, compliance audits, change traceability.

---

#### Check Key Configuration

```bash
kyde key
```

Displays current signing mode, algorithm, fingerprint, and key locations.

**Use case**: Deployment verification, security posture checks.

---

## Monitoring & Alerting

### ServiceNow Integration (Example)

1. **Create a MID Server endpoint** to KYDE `/api/metrics`
2. **Set up a REST Table API** to poll `/api/metrics` every 5 minutes
3. **Map metrics to Availability Management KPIs**:
   - `signature_success_rate` → CI Health > Application Health
   - `chain_integrity.valid` → CI Health > Data Integrity
   - `entries_per_hour_24h` → Usage Metrics

4. **Create alert rules**:
   ```
   IF signature_success_rate < 0.99 THEN create incident (High, Signing)
   IF chain_integrity.break_count > 0 THEN create incident (Critical, Ledger)
   IF uptime_seconds drops THEN alert (Availability)
   ```

### Datadog / New Relic Integration

Poll `/api/metrics` and push to custom metrics:

```python
import requests
import time

while True:
    resp = requests.get(
        "http://localhost:8501/api/metrics",
        cookies={"session": os.getenv("KYDE_SESSION")}
    )
    metrics = resp.json()

    # Example: Datadog
    datadog_api.Metric.send(
        metric="kyde.signature_success_rate",
        points=[(time.time(), metrics["signature_success_rate"])],
        tags=["env:prod", "service:kyde"]
    )

    time.sleep(300)  # Poll every 5 minutes
```

### Webhook for Incident Sync

Subscribe to `/api/incidents` and POST new incidents to your ITSM tool:

```python
last_checked = {}
while True:
    resp = requests.get(
        "http://localhost:8501/api/incidents",
        cookies={"session": token}
    )
    for inc in resp.json():
        if inc["id"] not in last_checked:
            # Send to ServiceNow, Jira, PagerDuty, etc.
            send_to_itsm(inc)
            last_checked[inc["id"]] = True

    time.sleep(60)  # Check every minute
```

---

## Audit & Compliance

### Regulatory Alignment

**SOC 2 Type II**:
- ✓ Tamper-evident audit trail (cryptographic signing)
- ✓ Change tracking (admin entries)
- ✓ Event logging (all agent actions)
- ✓ Access control (session-based auth)

**HIPAA**:
- ✓ Audit log (ledger entries with timestamps)
- ✓ Entity accountability (agent_id tracking)
- ✓ Data integrity controls (hash chaining + signatures)

**PCI-DSS**:
- ✓ Requirement 10 (logging & monitoring)
- ✓ Requirement 6.4 (separation of duties) — ledger read via dashboard, write via proxy

**GDPR**:
- ✓ Data processing logs (what agent accessed/processed)
- ✓ Right to audit (full entry history accessible)

### Audit Procedures

#### Monthly Integrity Check

```bash
kyde ledger verify > /tmp/monthly_audit_$(date +%Y-%m).log
```

Archive the log and store in a compliance system (e.g., SIEM).

#### Quarterly Key Rotation

```bash
# Back up old key
cp ~/.agent-ledger/signing.pub ~/.agent-ledger/signing.pub.backup.2026-Q1

# Generate new key
kyde keygen --type local --force

# Log the rotation (auto-logged to ledger)
kyde key  # Verify new fingerprint
```

#### Annual Compliance Report

```bash
# Gather metrics for the year
kyde ledger list > annual_audit.txt
kyde ledger verify >> annual_audit.txt

# Generate PDF report with:
# - Total entries processed
# - Signature success rate (target: > 99.9%)
# - Zero chain breaks
# - Key rotation history (from ledger)
# - Incident timeline (from /api/incidents)
```

---

## Troubleshooting

### Ledger Verification Fails

```
✗ Ledger integrity FAILED — 3 error(s):
  • [entry-abc123] Invalid signature: mismatched key
```

**Diagnosis**:
1. Check if public key file exists: `kyde key`
2. If signing mode switched from TPM to software (or vice versa), this is expected
3. If unexpected, it indicates potential tampering

**Recovery**:
- Isolate the ledger (stop proxy)
- Investigate the affected entry: `kyde ledger show entry-abc123`
- If hardware key was rotated, re-key: `kyde keygen --type [tpm|local] --force`

---

### Ledger Write Failures

```
⚠ Ledger write failed: connection refused / timed out
```

**Cause**: Postgres is unreachable — the container stopped, crashed, or the
network between gateway and Postgres is broken.

**Solution**:
1. `docker compose ps` — confirm the `postgres` service is `healthy`.
2. `docker compose logs postgres` — look for startup errors (bad volume
   permissions, exhausted disk, wrong password).
3. Writes are already serialized safely across processes via a Postgres
   advisory lock, so multi-worker gateways no longer risk chain corruption.
   You do *not* need sticky sessions.

---

### TPM Not Detected

```
✗ TPM not available. Install tpm2-pytss and ensure TPM device is accessible.
   pip install kyde-gateway[tpm]
```

**Diagnosis**:
```bash
# Check TPM is present
lsmod | grep tpm2
ls -la /dev/tpm* /dev/tpmrm*

# Check tpm2-tools
tpm2_getcap properties-fixed
```

**Solution**:
1. Install `tpm2-tools` (OS package)
2. Install `tpm2-pytss` (Python): `pip install kyde-gateway[tpm]`
3. Ensure TPM device permissions (usually needs root or special group)

---

### High Signature Failure Rate

If `/api/metrics` shows `signature_success_rate < 0.98`:

1. Check key files haven't been modified: `ls -l ~/.agent-ledger/`
2. Verify key fingerprint matches known value: `kyde key`
3. Review recent entries for errors: `kyde ledger list`
4. Check if TPM became unavailable: `kyde key | grep "TPM available"`

---

## Best Practices

1. **Run proxy and dashboard on separate hosts** (or at least separate ports) to reduce blast radius
2. **Use TPM for hardware-backed security** in high-trust environments
3. **Rotate keys quarterly** and archive old public keys for historical verification
4. **Monitor signature_success_rate** continuously—alert if it drops below 99.5%
5. **Archive ledger snapshots weekly** to cold storage for long-term compliance
6. **Automate incident ingestion** into your ITSM tool within 15 minutes
7. **Test ledger verification monthly** to catch corruption early
8. **Document your upstream configuration** (OpenAI vs. Anthropic vs. Gemini) in your CMDB
9. **Review `/api/incidents` daily** during the first 30 days post-deployment

---

## Support & Escalation

For issues:
1. Check this guide's [Troubleshooting](#troubleshooting) section
2. Review `/api/metrics` for health status
3. Run `kyde ledger verify` for integrity check
4. Collect logs: `kyde ledger list > logs.txt` + `/api/incidents` snapshot
5. Escalate to engineering with metrics + logs

---

## Next Steps (Phase 2)

After Phase 1 stabilizes, consider:

- **Change Advisory Board (CAB) Integration**: Policy engine for approving tool calls
- **RBAC**: Role-based access to specific agent IDs or tools
- **Ledger Export**: Automated compliance report generation
- **Real-time Streaming**: WebSocket feed of events for live dashboards
- **Cost Attribution**: Tie agent actions to billing/chargeback systems
