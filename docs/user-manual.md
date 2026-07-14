# Kyde Gateway — User Manual

> **Applies to Kyde Gateway `v0.3.0`** · last reviewed 2026-06-09.

This manual explains **how to use** the Kyde Gateway once it's deployed.
For deployment, upgrades, and backups, see the
[deployment guide](./deployment.md).

Two kinds of people log in every day and this manual addresses both:

- **Admins** — configure policy, manage users, read the audit surface
  without seeing message bodies.
- **Auditors** — review DLP alerts, inspect prompts and responses,
  decide on dispositions.

A third role, **viewer**, gets read-only browsing across the non-content
surfaces. Anything a viewer can do, an auditor and an admin can too.

---

## Table of contents

0. [Orientation](#part-0--orientation)
   - [When to reach for this manual](#when-to-reach-for-this-manual)
   - [Logging in](#logging-in)
   - [Anatomy of the dashboard](#anatomy-of-the-dashboard)
1. [Roles](#part-1--roles)
   - [1.1 The three roles](#11-the-three-roles)
   - [1.2 The "4-eyes" design principle](#12-the-4-eyes-design-principle)
   - [1.3 Capability matrix](#13-capability-matrix)
   - [1.4 What each role sees when they log in](#14-what-each-role-sees-when-they-log-in)
   - [1.5 Guardrails the system enforces](#15-guardrails-the-system-enforces)
2. [Features](#part-2--features-page-by-page)
   - [2.1 Fleet Status](#21-fleet-status)
   - [2.2 Threats & Alerts](#22-threats--alerts)
   - [2.3 Agent Chains](#23-agent-chains)
   - [2.4 Network Map](#24-network-map)
     - [2.4.1 Segment drill-down](#241-segment-drill-down)
     - [2.4.2 Agent view](#242-agent-view)
     - [2.4.3 IP view](#243-ip-view)
     - [2.4.4 Trust configuration](#244-trust-configuration)
     - [2.4.5 What this view cannot tell you](#245-what-this-view-cannot-tell-you)
   - [2.5 Agents](#25-agents)
   - [2.6 Agent Activity](#26-agent-activity)
   - [2.7 Hosts](#27-hosts)
   - [2.8 Sessions](#28-sessions)
   - [2.9 Audit Log](#29-audit-log)
   - [2.10 Compliance](#210-compliance)
   - [2.11 Usage & Cost](#211-usage--cost)
   - [2.12 Policies](#212-policies)
   - [2.13 DLP Rules](#213-dlp-rules)
   - [2.14 Users](#214-users)
   - [2.15 MCP Servers](#215-mcp-servers)
   - [2.16 AI Providers](#216-ai-providers)
   - [2.17 Labels](#217-labels)
   - [2.18 Pricing](#218-pricing)
   - [2.19 Admin Actions](#219-admin-actions)
   - [2.20 Settings](#220-settings)
     - [2.20.1 Overview — system & operational metrics](#2201-overview--system--operational-metrics)
     - [2.20.2 Runtime tuning](#2202-runtime-tuning)
     - [2.20.3 Email notifications (SMTP)](#2203-email-notifications-smtp)
     - [2.20.4 Signing & cryptography](#2204-signing--cryptography)
     - [2.20.5 Ledger](#2205-ledger)
   - [2.21 Profile](#221-profile)
3. [Common workflows](#part-3--common-workflows)
   - [3.1 Onboard a new auditor](#31-onboard-a-new-auditor)
   - [3.2 Triage a DLP alert end-to-end](#32-triage-a-dlp-alert-end-to-end)
   - [3.3 Silence a noisy pattern](#33-silence-a-noisy-pattern)
   - [3.4 Recover when all admins are locked out](#34-recover-when-all-admins-are-locked-out)
   - [3.5 Rotate the SMTP password](#35-rotate-the-smtp-password)
   - [3.6 Turn on inline DLP blocking (prevention)](#36-turn-on-inline-dlp-blocking-prevention)
4. [Appendix](#part-4--appendix)
   - [4.1 Glossary](#41-glossary)
   - [4.2 Keyboard shortcuts](#42-keyboard-shortcuts)
   - [4.3 Where errors surface](#43-where-errors-surface)
   - [4.4 Cross-reference table](#44-cross-reference-table)
   - [4.5 Supported agent frameworks](#45-supported-agent-frameworks)

---

## Part 0 — Orientation

### When to reach for this manual

| You want to… | Read |
| --- | --- |
| Stand up the stack, configure the host, back it up, upgrade it | [`deployment.md`](./deployment.md) |
| Configure SMTP, manage DLP policies & prevention, manage users, review alerts | this manual |

### Logging in

Open the admin hostname your deployer gave you (typically something like
`https://gateway.company.com/`) and sign in with the credentials you
were issued.

- **Very first login, fresh install.** If no admin exists yet, the
  dashboard routes you to `/setup` instead of `/login`. Create the
  initial admin account there (username is fixed as `admin`; pick a
  password that meets the policy: ≥ 12 chars, one upper, one lower, one
  digit, one special). You're logged in automatically after setup.
- **First login with a temp password.** New users (and users whose
  password an admin has just reset) land on `/change-password`
  immediately. You cannot reach any other page until you change the
  password. This is deliberate — temp passwords are single-use.
- **Locked out.** Three consecutive failed logins lock your account.
  Ask an admin to unlock you from the Users page. If every admin is
  locked out, see §3.4 for the CLI recovery path.

### Anatomy of the dashboard

Every page uses the same layout: a left sidebar with nav, a main pane
with the page content. The header carries the KYDE logo, a
notifications bell, a chain-status chip (VERIFIED / BROKEN), and a user
menu in the footer.

**The sidebar is role-specific.** Admins and auditors do not see the
same menu, and they land on different pages after login (admins on
Fleet Status, auditors on Threats & Alerts). The sidebar subtitle even
changes: "Agent Governance Console" for admins, "Compliance Evidence
System" for auditors. See §1.4 for the full per-role menus.

```
  Admin sidebar                    Auditor sidebar
+----------------------+        +----------------------+
|  KYDE  (Governance)  |        |  KYDE  (Compliance)  |
|  Fleet Status        |        |  Threats & Alerts    |
|  Threats & Alerts    |        |  Policies            |
|  Agent Chains        |        |  Sessions            |
|  Network Map         |        |  Audit Log           |
|  Agents              |        |  Compliance          |
|  Hosts               |        |  Agent Chains        |
|  Usage & Cost        |        |  Agent Activity      |
| —— Management ——     |        |  Network Map         |
|  Users               |        |  Agents              |
|  MCP Servers         |        |  Hosts               |
|  AI Providers        |        +----------------------+
|  Policies            |
| —— Operations ——     |
|  Labels              |
|  Pricing             |
|  Admin Actions       |
|  Settings            |
+----------------------+
```

A **viewer** sees a read-only subset of these pages (no management /
operations groups, no content). Anything a viewer can do, an auditor
and an admin can too.

Press `Cmd+K` (macOS) or `Ctrl+K` (Linux/Windows) anywhere to jump to a
page or to an entry by sequence number (type `#123`).

---

## Part 1 — Roles

### 1.1 The three roles

**`admin`** is the operator role. Admins configure the gateway — DLP
policies & inline prevention, SMTP, user accounts, upstream providers,
MCP servers, pricing — and read the governance surfaces (Fleet Status,
Compliance, Audit Log, Sessions, Usage & Cost, Network Map, Threats &
Alerts). Admins **cannot see the contents of user conversations** (see
§1.2); that's intentional.

**`auditor`** is the review role. Auditors see the actual message
bodies (request/response text, DLP finding contents) — the **only**
role that does. They are the ones who judge whether a DLP alert is a
real leak, a benign true positive, or noise. Auditors can manage DLP
regex policies (including per-pattern prevention) but cannot manage
users, settings, MCP servers, or the prevention master switches — they
are reviewers, not operators. Auditors get their own sidebar and land
on Threats & Alerts.

**`viewer`** is the read-only baseline — simply "an authenticated user
with no elevated role". Viewers can browse the observability pages with
conversation bodies redacted (same as admins) and have no triage or
configuration rights. Good for SOC analysts or compliance staff who
need a high-level picture but not the full content view. There is no
separate viewer sidebar; it is the minimum role a user can hold.

A single user can hold multiple roles — typical combinations:
- `[admin]` — a pure operator.
- `[auditor]` — a pure reviewer.
- `[admin, auditor]` — small team where one person wears both hats.
- `[viewer]` — the minimum role; required since a user must have at
  least one role to exist.

### 1.2 The "4-eyes" design principle

The gateway deliberately **separates the ability to configure the
system from the ability to read what flows through it**.

- Admins see **metadata**: agent IDs, timestamps, model names, upstream
  providers, token counts, DLP alert counts, integrity status,
  signatures. Not bodies.
- Auditors see **content**: prompts, responses, the actual matched
  text inside a DLP finding, context snippets.
- Viewers see the same metadata as admins, with no write access.

This is enforced at the API — not just the UI. When a non-auditor opens
an entry detail, the `why` and `full_messages` fields are stripped and
a `content_redacted: true` flag is set on the response. When a
non-auditor opens the Threats & Alerts page, each finding's
`matched_value` and `context_snippet` are replaced with the placeholder
`<redacted — auditor role required>`.

In practice:
- As an **admin**, you can see *that* an alert happened, what pattern
  fired, what score, when, and who the agent was. The *what was in the
  message* is hidden.
- As an **auditor**, you see the above plus the actual matched text and
  the surrounding context. This is what lets you triage.

If a single person needs both views (e.g. a small team with only one
person), assign both `admin` and `auditor` roles to them. The gateway
treats roles additively.

**One deliberate carve-out.** The Compliance page's **Chain Signatures**
export (and the ledger evidence export) is available to admin *or*
auditor and, by design, includes the signed message content — it exists
so a compliance officer can hand a verifiable evidence bundle to an
external party. This is the single path by which a non-auditor admin can
obtain message content; everything in the live UI stays redacted for
them. Treat the export as sensitive.

### 1.3 Capability matrix

✓ = full access. **R** = visible but redacted (metadata shown, message
content hidden). ✗ = page/action not available to that role.

The rows match the real page set and reflect what each role can reach in
the UI. Note that `viewer` is a deliberately thin role — only three
pages are open to it.

| Capability / page | admin | auditor | viewer |
| --- | :---: | :---: | :---: |
| **Browse & read** | | | |
| Fleet Status | ✓ | ✗ | ✗ |
| Threats & Alerts (list + detail) | ✓ | ✓ | ✓ |
| ↳ finding `matched_value` / context | **R** | ✓ | **R** |
| Agent Chains | ✓ | ✓ | ✓ |
| Network Map | ✓ | ✓ | ✗ |
| Agents (list + detail) | ✓ | ✓ | ✗ |
| Agent Activity | ✓ | ✓ | ✗ |
| Hosts | ✓ | ✓ | ✗ |
| Sessions (list + detail) | ✓ | ✓ | ✗ |
| ↳ session message content | **R** | ✓ | — |
| Audit Log (list + entry metadata) | ✓ | ✓ | ✗ |
| ↳ entry message bodies | **R** | ✓ | — |
| Compliance | ✓ | ✓ | ✗ |
| Usage & Cost | ✓ | ✗ | ✗ |
| **DLP triage (Threats & Alerts)** | | | |
| Claim / start / need-info / close | ✓ (*lead*) | ✓ (*analyst*) | ✓ (*analyst*) |
| Escalate / reopen | ✓ | ✗ | ✗ |
| Mute a firing pattern (per-pattern disable) | ✓ | ✓ | ✗ |
| Add finding to allowlist ("Add to Policy") | ✓ | ✗ | ✗ |
| Block the offending agent | ✓ | ✗ | ✗ |
| **DLP configuration** | | | |
| Policies page (view) | ✓ | ✓ | ✗ |
| Toggle pattern Enabled / per-pattern Prevention | ✓ | ✓ | ✗ |
| Prevention **master** switches (global block) | ✓ | ✗ | ✗ |
| DLP Rules (allowlist) page + CRUD | ✓ | ✗ | ✗ |
| **Admin / operations** | | | |
| Users (CRUD, reset, unlock, delete) | ✓ | ✗ | ✗ |
| MCP Servers (registry + tool policies) | ✓ | ✗ | ✗ |
| AI Providers / Labels / Pricing | ✓ | ✗ | ✗ |
| Admin Actions (admin audit log) | ✓ | ✗ | ✗ |
| Settings (all subsections) | ✓ | ✗ | ✗ |
| Agent block / rename / traffic-mode flip | ✓ | ✗ | ✗ |
| **Compliance evidence export** | | | |
| Compliance report / evidence / incident PDF | ✓ | ✓ | ✗ |
| Ledger CSV / Chain Signatures (incl. content) | ✓ | ✓ | ✗ |
| **Self-service** | | | |
| Profile (own email / password) | ✓ | ✓ | ✓ |
| Command palette (Cmd+K) | ✓ | ✓ | ✓ |

A note on triage: the state machine maps `admin → lead` and every
non-admin role → `analyst`. Analysts can drive an alert through the
normal lifecycle (claim → investigate → close); only leads (admins) can
**escalate** or **reopen**.

### 1.4 What each role sees when they log in

Admins and auditors get different menus and different landing pages.
A viewer sees only the always-open pages.

```
 Admin (lands on Fleet      Auditor (lands on          Viewer
 Status)                    Threats & Alerts)
 ─────────────────────      ─────────────────────      ─────────────────────
 Fleet Status               Threats & Alerts           Threats & Alerts (R)
 Threats & Alerts           Policies                   Agent Chains
 Agent Chains               Sessions                   Profile
 Network Map                Audit Log
 Agents                     Compliance
 Hosts                      Agent Chains
 Usage & Cost               Agent Activity
 — Management —             Network Map
 Users                      Agents
 MCP Servers                Hosts
 AI Providers
 Policies
 — Operations —
 Labels
 Pricing
 Admin Actions
 Settings
 ─────────────────────      ─────────────────────      ─────────────────────
 Sees: metadata only        Sees: metadata + bodies    Sees: metadata only
 Can:  configure system     Can:  judge alerts,        Can:  read only
                                  manage DLP policies
```

(R) = redacted content. A viewer is any authenticated user with no
`admin` or `auditor` role.

### 1.5 Guardrails the system enforces

These rules protect you from misoperation — you can't disable them
from the UI.

- **Last-admin protection.** The system refuses to delete or disable
  the only remaining admin account, and refuses to remove the `admin`
  role from it. You must promote another user first.
- **No self-elevation to auditor.** An admin cannot grant themselves
  the `auditor` role. Another admin must do it. This preserves 4-eyes
  even when one human holds both hats over time.
- **No self-delete.** You cannot delete your own account. Ask another
  admin.
- **Lockout after 3 failed logins.** An admin can clear the lockout
  from the Users page. If every admin is locked out, use the CLI
  rescue path (§3.4).
- **Temp passwords are one-shot.** Anyone logging in with a
  freshly-reset password is forced to `/change-password` on arrival.

---

## Part 2 — Features, page by page

The pages below appear in sidebar order (admin sidebar). Each chapter
says who can see the page, what it's for, and how to do the main things
on it. Pages reachable only by auditors are noted in their "Who can see
it" line.

### 2.1 Fleet Status

**Who can see it:** admin (this is the admin landing page).

The operational at-a-glance page. The top of the page is a large
**status hero** that reads `OPERATIONAL` (green), `WARNING` (amber), or
`BREACH` (red):

- **BREACH** — the ledger chain failed verification, or there is at
  least one open `CRITICAL` alert.
- **WARNING** — open `HIGH` or `MEDIUM` alerts exist.
- **OPERATIONAL** — none of the above.

Below the hero:

- **Four KPI cards:** Active Agents · Open Alerts (click → Threats &
  Alerts) · Blocked Chains (24h) (click → Agent Chains) · Data Integrity
  (VERIFIED / BROKEN).
- **Agent Activity (last 14 days)** — a line chart with a grey ±2σ
  baseline corridor, a dashed average line, and red dots marking
  anomalous days.
- **Top 5 Active Agents** — a horizontal bar chart; each bar is colored
  green / amber / red by that agent's worst open alert.
- **Recent Activity** — the last few open alerts with severity filter
  chips (All / CRITICAL / HIGH / MEDIUM / LOW, each with a count); each
  row links into Threats & Alerts.

Use this page to answer "is anything wrong right now?" at a glance.
Auditors get an equivalent starting point on Threats & Alerts instead.

### 2.2 Threats & Alerts

**Who can see it:** everyone. Finding content is redacted for
non-auditors. This is the auditor landing page.

The operational hot path for review. Every request through the gateway
is scanned by two DLP engines (a neural classifier and a regex engine)
before the response returns; findings above threshold raise an alert
here. Requests blocked inline by DLP prevention (see §2.12) also land
here, flagged with a **"Prevented"** badge.

**Top of page:** four colored severity boxes (CRITICAL / HIGH / MEDIUM /
LOW) counting open alerts, then two filter rows:

- **Source:** All sources / Chat / MCP.
- **Status:** Open / In Review / Escalated / Closed / All.

**Alerts table columns:** select checkbox · Alert ID (`ALR-####`) ·
Severity badge · Type · Agent · Detected (relative) · Status · Action
(Show chain → / Details →). The Type cell can carry a red **"Prevented"**
badge (request was blocked inline) and a blue **"MCP · server · tool"**
badge (alert came from an MCP call rather than a chat completion).

**Bulk toolbar** (appears when you select rows): Close as False Positive
· Close as Confirmed Leak · Assign to me · Export PDFs · Clear selection.

**Detail sheet** (slides in from the right when you click a row):

```
+-----------------------------------------------+
|  ALR-1042   [HIGH]  [Prevented]          [X]  |
|  Agent  research-agent-v1   Detected  3m ago  |
|  Source MCP · github · create_issue           |
|-----------------------------------------------|
|  Findings — 3 across 2 patterns               |
|   [MEDIUM] [pii] Email Address      75%       |
|     Match: juer***@***.com    [Add to Policy] |
|     Context: "... user's email is juer..."    |
|   > [MEDIUM] [credential] SQL Injection  50%  |
|-----------------------------------------------|
|  Actions                                      |
|  [Start review] [Escalate] [False positive]   |
|  [Confirm incident]  [Disable pattern]        |
|  [Add to Policy (admin)] [Block agent (admin)]|
|-----------------------------------------------|
|  Triage Events (timeline)                     |
+-----------------------------------------------+
```

Non-auditors see `<redacted — auditor role required>` in place of
Match / Context. Auditors additionally get an "Auditor Notes" field and
"Export as Evidence". "Disable pattern" (mute a noisy regex pattern) is
available to admin **and** auditor; "Add to Policy" (allowlist) and
"Block agent" are **admin-only**.

**Triage workflow (state machine).** An alert starts at `new`; the only
terminal state is `closed`. Who can drive each transition:

| From → to | Who |
| --- | --- |
| `new` → `claimed` / `in_progress` | analyst, lead |
| `new` → `escalated` | **lead (admin) only** |
| `new` → `closed` | analyst, lead, system (allowlist match) |
| `claimed` / `in_progress` → `pending_info` | analyst, lead |
| `* ` → `escalated` | **lead only** |
| `pending_info` → `in_progress` | analyst, lead |
| any open → `closed` | analyst, lead |
| `escalated` → anything | **lead only** |
| `closed` → `claimed` (reopen) | **lead only** |

The detail sheet only shows buttons you're allowed to use. A non-admin
viewing a closed alert sees "Alert closed. Only admins can reopen."

**Closing an alert** requires a disposition: `false_positive`,
`benign_true_positive`, `policy_violation`, `confirmed_leak`,
`duplicate`, `allowlisted`, or `inconclusive`. An optional note is
stored in the alert's Triage Events timeline.

**Dedup.** Long conversations resend prior turns; the gateway keeps one
alert per unique leak and bumps `seen_count` rather than creating
duplicates. Two distinct emails stay two alerts; the same email across
30 turns stays one.

### 2.3 Agent Chains

**Who can see it:** everyone.

A **chain** is a session viewed as a tool-call trajectory — the ordered
sequence of steps an agent took (chat → tool_call → tool_result → …),
rendered to show where policy intervened.

- **Status hero:** `BLOCKED` / `OBSERVED` / `ALLOWED`.
  - `BLOCKED` — a step was stopped inline by DLP prevention (a
    `policy_block` entry / "Prevented" alert).
  - `OBSERVED` — an open DLP alert exists on the chain but nothing was
    blocked.
  - `ALLOWED` — clean.
- **KPIs** summarize the chain (steps, agents, alerts).
- **Step visualizer:** a horizontal row of steps, each with a
  COMPLETED / BLOCKED / PREVENTED icon. Click a step to open its alert
  detail sheet or the underlying entry dialog.
- **Filter chips:** Incidents / Blocked / Observed / Allowed / All.
- **Action panel (role-split):** admins see Acknowledge / Add to Policy /
  Export; auditors see a Disposition select + Notes.

Use this to see, for one agent task, exactly which step tripped a policy
and what happened next — the narrative view that complements the flat
Audit Log (§2.9).

### 2.4 Network Map

**Who can see it:** admin or auditor.

A network-shape view of where your agents actually sit. The ledger
already knows **who** asked (agent_id) and **what** they asked; this
page adds **where** — the proxy chain in front of the gateway,
classified into private (RFC1918 / CGNAT / IPv6 ULA) vs public
segments, and parsed per-tool (Cursor, Copilot, Claude Code, raw
SDKs).

**Five KPIs** across the top: Total Nodes · Network Segments · AI
Providers · Models · **Unknowns** (unattributed nodes — turns amber
when > 0; an "Investigate →" table appears when present).

**The main view** is a five-layer Sankey:

```
network segment  →  agent  →  KYDE Gateway  →  AI provider  →  model
```

Each flow is sized by request count. Segment-layer nodes are colored by
origin class so public-internet origins stand out from corporate-internal
ones. Clicking any flow opens a **side-sheet** (request count, first/last
seen, top agents, recent sessions). A time-window selector
(1h / 24h / 7d / 30d) top right scopes the page; data auto-refreshes.

**What to read off it:**

- **Many public segments from one customer** — their corp LB isn't
  setting `X-Forwarded-For`, or a VPN is funneling everyone through
  one exit point. See §2.4.4 for the trust config that reveals more
  of the chain.
- **One tool spread across many segments** — developers using the
  same SDK from home, office, and VPN. Normal mobility.
- **An AI provider you didn't know you had** — a misconfigured agent
  pointing at a provider you don't control, or shadow-IT that escaped
  the approved list.
- **High Unknowns** — traffic the gateway couldn't attribute to a known
  segment/agent; investigate via the unattributed-nodes table.
- **Agents all on one RFC1918 segment** — if that segment is your
  Docker bridge (e.g. `172.18.0.0/24`), you're seeing the container
  network, not the real client. A deployment-config issue, not a data
  issue; see §2.4.4.

#### 2.4.1 Segment drill-down

Click any **segment** node in the Sankey (the leftmost layer) to
open the segment detail page: three tables — agents, IPs, and
recent sessions observed under that CIDR. Every `agent_id` and IP
on the page is a link: clicking drops you into the corresponding
detail view, so the graph becomes an investigative loop.

Use this for: *"What is coming out of `10.4.0.0/24` right now, and
is anything new or unusual on it?"*

#### 2.4.2 Agent view

Reach it by clicking any `agent_id` — on the segment page, on an IP
page, or from the Sessions page. Shows, for the selected time
window:

- **Segments** the agent has been seen under. *Same `agent_id`
  spread across multiple segments* is the signal: a developer
  working from home + office, a service hitting you from multiple
  pods, or a shared API key being reused across devices.
- **IPs** the agent connected from, with request counts per IP.
- **Tools / Upstreams / Models** breakdowns — what this agent
  actually uses.
- **Recent sessions** — each row links into the existing session
  detail (§2.8).

Use this for: *"What does this one agent do, everywhere it
appears?"*

`agent_id` is the right lens when you care about a logical identity
— it stays stable across IP changes, VPN toggles, and pod restarts,
unlike the IP view.

#### 2.4.3 IP view

Narrower, more forensic. Reach it by clicking any IP on the segment
or agent view. Shows, for the selected time window:

- The parent **segment** (class + CIDR) — click through to drill the
  whole segment back out.
- **Agents** seen from this IP. *Multiple agents on one IP* catches
  shared NAT egresses — one corp NAT address for many developers,
  one pod IP for many session workers.
- Tools / Upstreams / Models / Sessions — same shape as the agent
  view, but scoped to a single physical address.

Use this for: *"That one IP looks wrong — show me everything that
came from it."*

IPs are less stable than `agent_id` (DHCP, VPNs, re-scheduled pods)
but more concrete for incident response.

#### 2.4.4 Trust configuration

A proxy chain is only as trustworthy as the hops that reported it.
The gateway walks each request's `X-Forwarded-For` right-to-left,
trusting a hop only if the IP that added it sits in
`TRUSTED_PROXY_CIDRS`. Anything before the first untrusted reporter
is discarded as client-spoofable.

Default trust list covers loopback + RFC1918 + IPv6 ULA — right for
gateways sitting behind a private load balancer. If your gateway is
fronted by Cloudflare, Fastly, AWS ALB, or similar, you **must**
extend `TRUSTED_PROXY_CIDRS` with those ranges in Settings →
Runtime tuning, otherwise the view will stop at the public edge IP
and miss the real client.

Config keys (see §2.20.2):

- `TRUSTED_PROXY_CIDRS` — comma-separated CIDR list. Adding a range
  means "I trust anything this range adds to `X-Forwarded-For`".
  Changes land within ~5 seconds; no redeploy.
- `NETWORK_ORIGIN_ENABLED` — master switch. Default on. Turn it off
  to skip per-request network capture; the page will still render
  historical data but new traffic won't appear.

**Docker-compose quirk.** If you see your entire traffic funneled
into a single Docker bridge range (typically `172.18.0.0/24`) and
are testing from the host, that's not a bug — Docker's userland
port-publishing proxy terminates the client connection at the
bridge, so nginx sees the bridge gateway (`172.18.0.1`) as the
peer. Two options to unmask it: (1) test from a second machine on
your LAN; (2) set `"userland-proxy": false` in
`/etc/docker/daemon.json` and restart Docker, which switches port
publishing to kernel NAT and preserves the real client IP.

#### 2.4.5 What this view cannot tell you

- **Inside the same corporate NAT.** If all of a customer's
  developers egress through one corp firewall IP (classic
  enterprise), you see one segment for the whole org. Use
  `agent_id` for per-user granularity — the network view is a map
  of infrastructure, not of people.
- **Geography.** This release intentionally ships no GeoIP layer.
  Corporate egress IPs mostly identify a data center, not the
  developer's location. A "public segments only" geo drill-down is
  planned for a later release.
- **What a proxy doesn't disclose.** We see exactly what the
  nearest proxy puts in HTTP headers. A proxy that strips
  `X-Forwarded-For` is effectively invisible to this view.
- **Pre-capture history on fresh installs.** On first run after
  upgrade, historical rows are backfilled from the stored
  `client_ip` (one hop) — richer chains only appear for traffic
  captured *after* the upgrade, when full headers are parsed.

### 2.5 Agents

**Who can see it:** admin or auditor.

The roster of every agent identity the gateway has seen (the
`X-Agent-ID` header, or a hash of the API key when the client didn't
identify itself).

**List columns:** Agent · Sessions · Entries · First seen · Last seen ·
Status (active / idle dot). KPIs across the top: Total · Active (24h) ·
With display name. Search filters the list; rows open the agent detail.

**Agent detail** (`/agents/:id`):

- KPIs: Requests (30d) · Sessions · Tokens (30d) · Cost (30d).
- Breakdown cards: Tools · AI providers · Models; a cost-by-model table;
  Segments observed; Hosts observed; Recent sessions.
- **Traffic inventory** — per-agent metering (see below).
- **Admin-only controls:** inline rename, Block / Unblock. A blocked
  agent's requests are rejected at the proxy.

**Traffic inventory (metering).** A table of per-endpoint counters for
the agent — one row per request kind (chat, MCP, other) with a request
count, last-seen time, and a **mode badge**: `count only` or
`full logging`. Admins get a per-row toggle to opt an endpoint into full
ledger logging (the chat row is always logged, so its toggle is hidden).

> Note: this is **Phase B1 — metadata-only**. The counters and mode flag
> are live, but the proxy does not yet capture full bodies for
> `full_logging` rows; that wiring lands in a later release. Don't rely
> on full-logging capture for non-chat traffic yet.

### 2.6 Agent Activity

**Who can see it:** admin or auditor.

A forensic analytics view across all agents for a selected time window.
Charts: activity over time (with average and +2σ reference lines), top
agents bar, and model / provider / action-type pie charts. A
Tokens/Calls metric toggle switches what the charts measure. Below the
charts, an extended agent table; clicking an agent opens a modal detail
dialog. Use this when you're investigating "what changed" rather than
watching live status.

### 2.7 Hosts

**Who can see it:** admin or auditor.

The roster of upstream/destination hosts the gateway has resolved, each
tagged with a **source chip** showing how its label was derived:
`admin` (you set it on the Labels page, §2.17), `dns` (reverse-DNS
succeeded), `dns-miss` (lookup returned nothing), or `none`. Rows open a
host detail view. Use it alongside the Network Map to put human-readable
names on IPs.

### 2.8 Sessions

**Who can see it:** admin or auditor. Message content is redacted for
non-auditors.

A conversation-level view on top of the ledger. A **session** is one
agent conversation, fully hash-chained: the gateway reconstructs it from
per-turn content hashes (the `session_turns` table), so a long
conversation that the client compacts still reads as a single session
and survives restarts.

**Left pane — session list:** a search box plus four filters — time
window (1h / 24h / 7d / 30d / 90d / All), sort (Newest / Oldest / Most
entries / Most agents), has-alert (Any / With alerts / No alerts), and
an agent filter. Each session card shows the session ID (`SES-####`),
entry count, a red "⚠ N" open-alert badge, a display name, agent count,
and last-seen time. Infinite scroll.

**Right pane — session detail:**

- Four MetricCards: Entries · Agents · Start · End.
- Chips linking each distinct agent (→ agent detail) and each host
  (rendered "hostname (ip)" → host detail).
- **🛡 Export Evidence** — a signed PDF for this session — and a
  "Full audit trail →" link into the Audit Log filtered to the session.
- A vertical **entry timeline** with colored node dots per action type
  (chat, tool_call, tool_result, error, `policy_block`, auth). Each row
  shows relative time, an action badge, model, role/kind chips, an
  expandable body (auditor-only content), tool-call names, and an inline
  red "⚠ DLP alert(s)" box whose chips open the alert detail sheet.

A footer notes the entries are an immutable, signed ledger.

### 2.9 Audit Log

**Who can see it:** admin or auditor.

A paginated table of every ledger entry — the request/response
interactions that went through the gateway. (This is the page formerly
called "Entry Timeline".)

- **KPI row:** Total Entries (with "Showing N of M") · Chain Integrity
  (VERIFIED / BROKEN) · Signature Failures · Date Range.
- **Filters:** search box (press `/` to focus, `Esc` to clear), time
  window, action type, AI provider, Clear. Arriving via a `?session=`
  or `?agent=` deep link shows an inbound-filter banner.
- **Table columns:** Seq (`SEQ-####`) · Action · Agent (→ detail) ·
  Session (`SES-####` → detail) · AI Provider · Model · Prompt (tokens) ·
  Response (tokens) · Alert (red "⚠ N") · Time. Hovering a row shows a
  `why_preview` tooltip (auditor-only).
- **Header actions:** Export CSV, 🛡 Export PDF.

**Entry detail dialog** — click any row. Four tabs:

- *Metadata* (everyone) — seq, timestamps, agent ID, model, provider,
  client IP, session ID, token counts, user agent, entry ID.
- *Messages* (**auditor only**) — the full captured request/response
  history. Non-auditors get an empty tab and a `content_redacted` notice.
- *Tools* (everyone) — extracted tool calls (function + args). Arg
  *values* follow the same redaction rule as Messages.
- *Hashes* (everyone) — input/output/prev/entry hash + signature; what
  makes the ledger tamper-evident.

Previous / Next chevrons step between entries without closing.

### 2.10 Compliance

**Who can see it:** admin or auditor. (Formerly "Data Integrity".)

Verifies the entire ledger's hash chain and every signature, and frames
the result as compliance evidence.

- **Hero:** `COMPLIANT` (green) / `NON-COMPLIANT` (red), driven by chain
  verification, with a context line (entry count, chain breaks, signature
  failures).
- **KPIs:** Ledger Entries · Chain Integrity · Signing Mode.
- **Detail cards:** Verification Details; Signing Status; Evidence Export
  (time-window picker + buttons: Full Compliance Report PDF, Ledger CSV,
  Chain Signatures JSON, Audit API → docs).
- **Public Key Fingerprint** (copyable) — share with verifiers so they
  can independently check the ledger.
- **Integrity Errors** list (entry IDs) when the chain is broken;
  **Verification History** (pass/fail rows from real verification runs).
- **Evidence Coverage** cards per framework — EU AI Act, DORA, NIS-2,
  GDPR Art. 30 — each with a COVERED / PARTIAL badge and per-article
  check/✗ icons. These derive from live ledger / DLP / signing signals,
  not from alert counts.

If the hero ever reads NON-COMPLIANT, read the Integrity Errors list,
then see the deployment guide's troubleshooting section.

### 2.11 Usage & Cost

**Who can see it:** admin. (Formerly "Token Analysis".)

Cost and usage analytics, with **EUR cost** as the headline metric.

- **KPIs:** Total Cost (EUR, with the fx rate shown) · Total Tokens ·
  Prompt / Completion · Active Agents. A date-range picker scopes the
  page.
- **Token Usage Over Time** — stacked bar (prompt + completion) with a
  dashed average line.
- **By Agent / By Model / By AI Provider** — horizontal stacked-bar
  breakdowns.
- **Agent Breakdown table** — Agent · Prompt · Completion · Tokens ·
  Cost (EUR), with "Show N more agents".

Use this to catch an agent or model suddenly burning tokens (and money)
at many times its normal rate. Per-model prices come from the Pricing
page (§2.18).

### 2.12 Policies

**Who can see it:** admin or auditor. The prevention **master switches**
require admin; auditors can still toggle per-pattern controls.

This is where DLP detection and **inline prevention (blocking)** are
controlled. (Don't confuse it with DLP Rules, §2.13, which is only an
allowlist.)

**Prevention card (top).** Two global master switches, shown as red
toggles:

- **Policy Prevention** (`DLP_REGEX_PREVENTION`) — when active, any
  regex/policy hit that is opted into prevention **blocks the request
  with HTTP 403** before it reaches the upstream. Opt-in is per pattern
  (see the table below).
- **BERT Prevention** (`DLP_BERT_PREVENTION`) — gateway-wide neural
  blocking; not per-pattern.

The card notes that scanner outages **fail open** (the request is
forwarded and a high-severity incident is raised, rather than taking the
gateway down). Two bulk buttons — **Enable all** / **Disable all** —
flip prevention on every pattern at once. A **Re-sync to dlp-regex**
button at the top of the page pushes pattern enable/disable state to the
regex sidecar.

**Policies table** (grouped by source, each group header showing
"N patterns · X disabled · Y preventing"):

| Column | Meaning |
| --- | --- |
| Name (+ id) | The pattern. |
| Pattern | The regex, truncated. |
| Category / Severity | Classification + severity badge. |
| Hits / Last hit | Match counters. |
| **Enabled** (green toggle) | Whether the pattern raises alerts at all. Disabling stops alerting gateway-wide. |
| **Prevention** (red toggle) | Per-pattern block-vs-observe. Only bites when the global **Policy Prevention** master switch is active. |

So: **Enabled** controls *detection* (does it alert?), **Prevention**
controls *blocking* (does it 403?), and blocking additionally requires
the master switch. See §3.6 for the end-to-end "turn on blocking" recipe.

### 2.13 DLP Rules

**Who can see it:** admin only.

The **allowlist** — and *only* the allowlist. Each row says "findings
matching this are benign; drop them before they become alerts." There
is no blocking/prevention control here; that lives on Policies (§2.12).

Stated on the page: "Allowlist findings that are known to be benign so
they no longer raise alerts or send emails."

**Columns:** Kind (green `allow` badge — `allow` is the only v1 kind) ·
Scanner (regex / bert / any) · Entity type · Match (exact text, or blank
for a broad rule) · Scope ("exact match" vs "entity type") · Note ·
Hits · Last hit · Added by · Added · delete (trash → confirm).

**Two ways to add a rule:**

1. **From an alert finding** (preferred) — the **Allowlist** button on a
   finding card in Threats & Alerts (§2.2). It pre-fills scanner, entity
   type, and matched text, and offers a scope choice: "Only this exact
   match" vs "Every {type} match (broad)". *(Creating the rule is
   admin-only.)*
2. **Add rule** on this page — pick Scanner, Entity type (required;
   case-insensitive), optional Match text (blank = allowlist every match
   of that type), optional Note.

**Matching is forgiving.** For regex findings the rule's entity type is
compared against the finding's `pattern_id`, `pattern_name`, and
`entity_type` — whichever matches first wins, all case-insensitive — so
you don't need to know the internal naming.

Hit counters include retrospective sweeps (the "Reapply allowlist"
action run over open alerts). Remove a rule with the trash icon; future
matches start alerting again.

### 2.14 Users

**Who can see it:** admin only.

Create, edit, disable, and delete user accounts. An "Add user" button
sits at the top; a **Show deleted** checkbox reveals soft-deleted rows
(shown dimmed). Columns: Username (with a "you" tag for yourself) ·
Email · Roles (color chips) · Status · Created · Actions (⋯).

**Add a user.** Click **Add user**. Fill in username (unique), email
(recommended — auditors need an email to receive alert notifications),
password, and at least one role (checkboxes: admin / auditor / viewer).
The dialog shows the new user's temp password — copy it, share it over a
secure channel; on first login they're forced to change it.

**Edit a user.** ⋯ → **Edit**. **Username is read-only** once created;
email and roles are editable. You cannot remove your own `admin` role if
you're the only admin, and you cannot grant yourself `auditor` (another
admin must).

**Reset password.** ⋯ → **Reset password** — generates a new temp
password (shown once), invalidates all that user's sessions, forces a
change on next login.

**Unlock.** ⋯ → **Unlock** clears the failed-login counter and the
lockout timestamp (the `locked_at` field) after three failed logins.

**Delete.** ⋯ → **Delete** — a *soft* delete (`deleted_at` set, sessions
invalidated, dimmed in the list with Show deleted). You cannot delete
yourself or the last admin.

**Role chips:** `admin` red (the keys), `auditor` blue (content access),
`viewer` muted (read-only).

### 2.15 MCP Servers

**Who can see it:** admin (the aggregator tool catalog is readable by
any authenticated user).

Manage the registry of Model Context Protocol servers the gateway
proxies (see the [reference](./reference.md#mcp-routing), "MCP routing").

- **Server list / add / edit / delete** — register a backend MCP server
  by name and base URL; the name becomes the `/mcp/{name}` path prefix.
- **Probe tools** — fetch a server's `tools/list` to populate the
  aggregator catalog. You paste a one-off bearer token for the probe;
  it is **never stored**.
- **Per-tool policies** — `(server, agent, tool) → allow / deny` rules
  (with `*` wildcards), enforced at proxy time. Use these to allow only
  specific agents to call specific tools.

### 2.16 AI Providers

**Who can see it:** admin.

A read-only grid of every upstream LLM provider (openai, anthropic,
gemini, copilot, plus anything you added in `config.yaml`). Each card
shows the base URL and a copy button, and you can copy the ready-made
agent endpoint URL to hand to developers. To add or change a provider,
edit `config.yaml` and restart the service — see [`deployment.md`](./deployment.md) §10.2.

### 2.17 Labels

**Who can see it:** admin.

Assign friendly, human-readable labels to host IPs. Labels you set here
appear on the Hosts page (§2.7, source chip `admin`) and Network Map
(§2.4), making segments and destinations readable instead of bare IPs.
Create, edit, and delete label entries here.

### 2.18 Pricing

**Who can see it:** admin to edit; the price list is read-only to other
roles.

The per-model price table that drives the EUR figures on Usage & Cost
(§2.11). Each row maps a model to its prompt/completion price. Edit a
row to correct a price or add a newly used model.

### 2.19 Admin Actions

**Who can see it:** admin.

The admin audit log — a record of privileged actions (user
create/edit/delete, role changes, settings updates, DLP policy and
prevention toggles, agent blocks, MCP changes), each with the acting
admin, target, and timestamp. This is your "who changed what" trail,
distinct from the request ledger (Audit Log, §2.9).

### 2.20 Settings

**Who can see it:** admin only.

Organized into five subsections in a left rail. A banner reminds you
that *most* configuration is file-based (see `docker-compose.yml` and
`config.yaml`) and read-only from the UI; the subsections below are the
exceptions.

#### 2.20.1 Overview — system & operational metrics

Read-only dashboards: service version, uptime, started-at, ledger size
on disk, total entries, entries/hour (24h and 1h), signature success
rate, tool-call ratio, chain integrity status. Refreshes every 30
seconds.

#### 2.20.2 Runtime tuning

Whitelisted settings you can change at runtime without a redeploy.
Each row has a Save and a Reset (back to env/default).

| Key | Default | Notes |
| --- | --- | --- |
| `PUBLIC_PROTOCOL` | `http` | `http` or `https`. |
| `PUBLIC_HOSTNAME` | `localhost` | What to put in copy-paste URLs for agents. |
| `PUBLIC_PORT` | `4000` | Proxy-listener port agents should use. |
| `TRUSTED_PROXY_CIDRS` | `127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1/128, fc00::/7` | Which hops in `X-Forwarded-For` to trust when reconstructing the proxy chain for Network Map (§2.4). Extend with your edge ranges (Cloudflare, AWS ALB, …) or the view will stop at the public edge IP. |
| `NETWORK_ORIGIN_ENABLED` | `true` | Master switch for the per-request network enrichment that feeds Network Map. Turn off to skip the side-table write on the hot path. |

Values resolve as `DB override → env var → hard-coded default`. A
change propagates across all workers within ~5 seconds. Every change
is signed into the audit ledger.

#### 2.20.3 Email notifications (SMTP)

This section configures alert emails to auditors. Recipients are
derived automatically from the Users table — every user with the
`auditor` role and a non-empty email gets the alerts. There is **no
separate mailing list**.

**Layout of the form:**

```
Email notifications
+---------------------------------------------+
|  [x]  Enable SMTP notifications             |
|                                             |
|  SMTP host        [smtp.relay.example.com]  |
|  Port             [587]                     |
|  Encryption       [STARTTLS (recommended) v]|
|  [x]  Verify TLS certificate                |
|                                             |
|  Username         [alerts@company.com    ]  |
|  Password         [••••••••] [Replace]      |
|                                             |
|  From address     [alerts@company.com    ]  |
|  From display     [Kyde Gateway Alerts   ]  |
|  Reply-To         [security@company.com  ]  |
|  Timeout (sec)    [10]                      |
|                                             |
|  Trigger policy   [Only on first detection v]|
|                                             |
|  N users with 'auditor' role will receive...|
|                                             |
|  [Save]   [Send test email]                 |
+---------------------------------------------+
```

**The 13 settings keys, with defaults and guidance:**

| Key | Default | Purpose |
| --- | --- | --- |
| `SMTP_ENABLED` | `false` | Master kill switch. When off, no alert emails go out regardless of other settings. |
| `SMTP_HOST` | — | Your relay, e.g. `smtp.sendgrid.net`. |
| `SMTP_PORT` | `587` | 587 for STARTTLS, 465 for implicit TLS, 25 only for test. |
| `SMTP_ENCRYPTION` | `starttls` | `none` \| `starttls` \| `tls`. Must match your relay and port. |
| `SMTP_USERNAME` | empty | Leave blank for IP-authenticated relays. |
| `SMTP_PASSWORD_ENC` | empty | The UI shows `••••••••` if a password is stored. Click **Replace** to enter a new one; save with the field empty to leave it unchanged. Stored AES-GCM-256 encrypted. |
| `SMTP_FROM_ADDRESS` | empty | Must be on a domain your relay is authorized to send from. |
| `SMTP_FROM_NAME` | `Kyde Gateway Alerts` | Display name shown in the recipient's mail client. |
| `SMTP_REPLY_TO` | empty | Optional. Useful when From is `no-reply@` but replies should reach a real inbox. |
| `SMTP_TLS_VERIFY` | `true` | Turn off only for self-signed relays inside your corporate network. |
| `SMTP_TIMEOUT_SECONDS` | `10` | Connect + send timeout. 10–30 s is fine. |
| `SMTP_TRIGGER_POLICY` | `first_detection` | See below. |
| `SMTP_MIN_SCORE` | `0.8` | Only used when trigger policy = `first_detection_min_score`. |

**The three trigger policies:**

- **`first_detection`** (default and recommended) — email on the very
  first time a unique leak is detected. Dedup repeats (same PII in
  later turns of the same conversation) bump the alert's
  `seen_count` but do **not** re-send. Lowest noise.
- **`first_detection_min_score`** — same as above, but only when the
  alert's score is ≥ `SMTP_MIN_SCORE`. Use this to squelch
  low-confidence noise when your auditors complain.
- **`every_scan`** — email on every detection, including dedup
  repeats. Noisy; useful only as a short-term diagnostic tool.

**Saving and testing.** Fill the form, click **Save**. Then click
**Send test email** — a canned message goes to every auditor using
the current config. If it fails, the error toast tells you why:

- `SMTP_ENABLED is off` — flip the toggle.
- `no users with the 'auditor' role have an email set` — the recipient
  list is empty. Go to the Users page and give at least one user the
  `auditor` role with an email.
- SMTP-level errors (auth failed, TLS handshake failed, connection
  refused) — fix the config and try again.

**AES-GCM key caveat.** The SMTP password is encrypted at rest with a
key stored in `kyde-store:~/.agent-ledger/smtp_aes.key`. The key is
auto-generated on first start. It is **not chain-critical**: losing
the key doesn't corrupt the ledger or the DLP alerts. What it *does*
mean is that every previously-encrypted SMTP password becomes
unrecoverable, and an admin has to re-enter the password here (which
the system gracefully surfaces as a decrypt error on the next Save
attempt). Back up the key as part of your volume backup story (see
[`deployment.md`](./deployment.md) §9.1).

#### 2.20.4 Signing & cryptography

Read-only panel showing the mode (software Ed25519 vs TPM ECDSA), the
key type, the public-key fingerprint (copyable), and the paths to the
key files on the `kyde-store` volume.

To generate or regenerate keys, use the `kyde keygen` CLI inside
the container — see [`deployment.md`](./deployment.md) §7.2 (signing
key generation and backup).

#### 2.20.5 Ledger

Read-only panel showing the ledger storage backend and the current
entry count. (The upstream LLM provider grid moved to its own page —
see AI Providers, §2.16.)

### 2.21 Profile

**Who can see it:** everyone.

Self-service page to change your own email and password.

- **Email form** — one field, Save button. Updates immediately.
- **Password form** — current password (required), new password
  (must meet the policy — 12+ chars, upper/lower/digit/special),
  confirm. Eye icons toggle visibility while typing.

Changing your password does **not** log you out of other sessions.
For that, ask an admin to reset your password on the Users page (that
invalidates every session for your account).

---

## Part 3 — Common workflows

These recipes stitch multiple pages together for real tasks.

### 3.1 Onboard a new auditor

**Who:** admin.

1. Go to **Users** → **Add user**.
2. Fill in username and the auditor's email. Set a temp password or
   let the dialog generate one.
3. Check **auditor** role (and any others you want — typical is just
   `auditor`). Save.
4. The dialog displays the temp password. Copy it; share it with the
   auditor through a secure out-of-band channel (password manager,
   encrypted chat).
5. The auditor signs in, is forced to `/change-password`, picks a
   strong password, and lands on the dashboard.
6. Confirm: have them open an existing alert on **Threats & Alerts**.
   As an auditor they should see finding content (no
   `<redacted — auditor role required>` placeholders). As an admin
   you'll still see those placeholders on your own login — that's the
   4-eyes principle working.
7. If SMTP is enabled, the auditor now starts receiving alert emails
   automatically. Send a test (**Settings → Email → Send test email**)
   if you want to verify delivery.

### 3.2 Triage a DLP alert end-to-end

**Who:** auditor.

1. Receive the alert email. Click the **Review in dashboard** button;
   you land on the alert detail sheet in **Threats & Alerts**.
2. **Start review / claim** the alert. Status flips from `new` to
   `claimed` / `in_progress` and you become the assignee.
3. Review each finding card. The matched text and context are visible
   to you as an auditor.
4. If you need more information (e.g. ask the agent owner what the
   prompt was for), click **Need info** — status goes to
   `pending_info`. The alert waits for you to come back.
5. Once you have enough to decide, click **Close** → pick a
   disposition:
   - Benign match (the pattern fired but nothing sensitive leaked):
     `false_positive` or `benign_true_positive`.
   - Sensitive but acceptable under policy: `policy_violation`.
   - Actual leak: `confirmed_leak`. Consider also posting a
     `linked_incident` via the API if you track incidents externally.
6. Optionally type a note. Save.
7. The alert moves to `closed`. Its events timeline (visible in the
   detail dialog) records every transition with timestamps and actor.
8. If you later realize you closed too quickly, **only an admin** can
   reopen. Ping them with the alert ID.

### 3.3 Silence a noisy pattern

**Who:** admin (allowlisting and pattern-disable are admin actions;
auditors can mute from an alert or pair with an admin).

An auditor complains that `sql_injection_attempt` from OWASP CRS fires
on every query containing a `*`. There are two ways to quiet it,
depending on whether you want to suppress *specific benign values* or
*turn the whole pattern off*.

**Option A — allowlist benign matches (surgical, keeps the pattern on).**

1. Open a noisy alert in **Threats & Alerts** and click **Add to
   Policy** on the offending finding card.
2. Scope picker: choose **Every `sql_injection_attempt` match (broad)**
   since the whole pattern is the problem. Add a note. Save — the rule
   appears on **DLP Rules** (§2.13).
3. New findings matching the rule are dropped before they alert. (Open
   alerts already raised stay until closed; a "Reapply allowlist" sweep
   can retire fully-allowlisted ones.)
4. To undo, delete the rule on **DLP Rules**; matches start alerting
   again.

**Option B — disable the pattern outright (blunt, stops all alerting).**

1. Go to **Policies** (§2.12) and find `sql_injection_attempt` (groups
   are by source; OWASP CRS here).
2. Flip its **Enabled** toggle off. Alerting for that pattern stops
   gateway-wide. Auditors can also do this from an alert via "Disable
   pattern".
3. Click **Re-sync to dlp-regex** so the regex sidecar picks up the
   change.

Use Option A when only some matches are noise; Option B when the pattern
is wrong for your environment entirely. Neither option blocks
requests — that's prevention, covered next.

### 3.4 Recover when all admins are locked out

**Who:** anyone with SSH access to the host.

1. From the host, run the CLI rescue command (see
   [`deployment.md`](./deployment.md) §9.4):
   ```bash
   docker compose --env-file .env.prod \
     -f docker-compose.yml -f docker-compose.prod.yml \
     exec kyde-api kyde admin create-admin \
     --username rescue --email rescue@company.com
   ```
2. The command prints a temp password. Use it to log in as `rescue`.
3. Go to **Users**. For each locked admin: **Unlock**, then
   **Reset password** and hand them the new temp password.
4. Once the real admins are back in, log out as `rescue` and use
   another admin account to **Delete** the rescue user. The system
   blocks self-delete, which is why you need the real admins back
   first.

### 3.5 Rotate the SMTP password

**Who:** admin.

1. **Settings → Email notifications**. The password field shows
   `••••••••` if one is stored.
2. Click **Replace**. The field becomes editable and empty.
3. Paste the new password. The eye icon lets you verify you typed it
   correctly.
4. Click **Save**. The field goes back to `••••••••` on success —
   the plaintext is never returned by the API.
5. Click **Send test email**. A successful toast means delivery works
   with the new credentials. A "authentication failed" error means
   the password you entered was wrong or your relay rejected it.

Saving with the field empty leaves the stored password unchanged —
this is the expected UX when you're editing other fields without
rotating the password.

### 3.6 Turn on inline DLP blocking (prevention)

**Who:** admin (the master switches are admin-only).

By default the gateway *detects and alerts* but forwards every request.
Prevention makes it **block** offending requests with HTTP 403 before
they reach the upstream. Roll it out carefully:

1. Go to **Policies** (§2.12).
2. Decide the scope first. For regex/policy blocking, opt the specific
   patterns you want to enforce into prevention via each row's
   **Prevention** toggle (start narrow — e.g. only high-severity
   credential patterns). For neural blocking, you'll use the BERT
   master switch instead.
3. Flip the master switch:
   - **Policy Prevention** (`DLP_REGEX_PREVENTION`) — activates blocking
     for the regex patterns you opted in at step 2.
   - **BERT Prevention** (`DLP_BERT_PREVENTION`) — activates neural
     blocking gateway-wide (no per-pattern opt-in).
   (Or use **Enable all** to opt every pattern into prevention at once —
   blunt; prefer per-pattern at first.)
4. Test from an agent: send a request that trips an enforced pattern.
   You should get a **403** back, and a new alert appears on **Threats &
   Alerts** carrying a red **"Prevented"** badge.
5. Remember the safety behavior: if a DLP scanner is unreachable,
   prevention **fails open** — the request is forwarded and a
   high-severity incident is raised rather than blocking all traffic.

To stop blocking, turn the master switch off (detection/alerting
continues) or clear the per-pattern Prevention toggles.

---

## Part 4 — Appendix

### 4.1 Glossary

- **Agent ID.** Who sent the request — either the `X-Agent-ID` header
  the client set, or a hash of the API key if the client didn't
  identify itself explicitly.
- **Session.** A logical conversation, reconstructed from message
  content hashes (see §2.8). A single conversation that compacts or
  spans hours stays as one session.
- **Entry.** One request/response pair recorded in the audit ledger.
  Each entry is signed and linked to the previous one by hash.
- **Ledger chain.** The full series of entries, linked by
  `entry_hash` → `prev_hash`. Tampering with any entry breaks every
  subsequent hash — the Compliance page surfaces this immediately.
- **Dedup hash.** A fingerprint of a DLP finding's matched content.
  Two detections of the same leak across different entries share a
  dedup hash and collapse to one alert with `seen_count` > 1.
- **Seen count.** How many times the same unique leak has been
  detected under this open alert. Visible inside the alert's
  metadata.
- **Disposition.** The reviewer's judgment attached to a closed DLP
  alert: `false_positive`, `benign_true_positive`, `policy_violation`,
  `confirmed_leak`, `duplicate`, `allowlisted`, `inconclusive`.
- **Lead vs analyst (triage).** The triage state machine treats
  admins as `lead` and every non-admin as `analyst`. Escalation and
  reopen are lead-only.
- **`content_redacted`.** A flag the API sets on any response shown
  to a non-auditor, telling the UI to hide message bodies and finding
  contents.
- **Network segment.** A `/24` (IPv4) or `/48` (IPv6) CIDR derived
  from an agent's origin IP. Used on the Network Map page (§2.4)
  as the coarsest "where did this request come from" grouping.
- **Origin class.** The label the gateway assigns to an origin IP:
  `public`, `rfc1918`, `cgnat`, `loopback`, `link_local`,
  `unique_local_v6`, or `unknown`. Used for the colored chips on
  Network Map and as the drill-down class on the IP view (§2.4.3).
- **Trusted proxy.** An IP or CIDR that the gateway believes when it
  reports a prior hop in `X-Forwarded-For`. Configured via
  `TRUSTED_PROXY_CIDRS` in Runtime tuning; anything added to XFF by
  an IP outside this list is treated as potentially client-spoofed
  and stripped from the origin lookup.
- **Detection vs prevention.** *Detection* means a DLP finding raises an
  alert (controlled by a pattern's **Enabled** toggle and thresholds).
  *Prevention* means an offending request is **blocked with HTTP 403**
  before reaching the upstream — controlled on the Policies page (§2.12)
  by the per-pattern **Prevention** toggle plus a global master switch.
- **Policy Prevention / BERT Prevention.** The two global master
  switches on Policies that turn blocking on. `DLP_REGEX_PREVENTION`
  enforces regex patterns opted into prevention; `DLP_BERT_PREVENTION`
  enforces the neural classifier gateway-wide. Both fail open on scanner
  outage.
- **Prevented badge.** The red badge on a Threats & Alerts row marking
  an alert whose request was blocked inline by prevention.
- **Allowlist (DLP Rules).** A benign-finding suppression rule (DLP
  Rules, §2.13). Distinct from prevention: an allowlist *removes* an
  alert; prevention *blocks* a request.
- **Agent chain.** A session rendered as an ordered tool-call
  trajectory, with per-step status (Agent Chains, §2.3).
- **Traffic metering.** Per-agent, per-endpoint request counters with a
  `count only` / `full logging` mode (Agent detail, §2.5). Currently
  metadata-only (Phase B1).

### 4.2 Keyboard shortcuts

| Shortcut | Where | What |
| --- | --- | --- |
| `/` | Audit Log | Focus the search box. |
| `Esc` | Any search box | Clear search and unfocus. |
| `Cmd+K` / `Ctrl+K` | Anywhere | Open the command palette. |
| `#<n>` | Command palette | Jump to entry with sequence `n`. |

### 4.3 Where errors surface

- **Per-service docker logs** on the host:
  `docker compose -f docker-compose.yml --env-file .env logs -f <service>`.
- **Incident store** — ledger integrity issues and signing failures
  are emitted to an in-memory incident list; the Compliance page
  is the most visible surface for them.
- **SMTP worker logs** — the notification worker prints every send
  attempt with `✉ notifications: alert <id> sent to N recipient(s)`,
  skips as `◦`, and failures with the exact error. Tail the
  `kyde-api` logs to watch it live.
- **Toast notifications** in the UI surface the server's error
  message verbatim on any failed mutation (save settings, create
  rule, reapply allowlist, send test email).

### 4.4 Cross-reference table

For a topic not covered here, see [`deployment.md`](./deployment.md):

| Topic | Section in deployment.md |
| --- | --- |
| Installing the stack | §5 (dev) / §6 (prod) |
| TLS / reverse proxy setup | §6.3 |
| Signing key generation & backup | §7.2 |
| Pointing agents at the gateway | §7.3 |
| Nightly Postgres backup | §9.1 |
| Upgrading versions | §6.4 |
| Stop / start / destroy | §9.3 |
| Last-admin lockout CLI recovery | §9.4 |
| Env-var reference | §10.1 |
| Upstream routing (`config.yaml`) | §10.2 |
| DLP pattern packs | §10.3 |
| Troubleshooting | §11 |

### 4.5 Supported agent frameworks

The gateway is SDK-agnostic: it governs an agent through two interception
points, so "supported" means the framework lets you hit both.

- **LLM leg** — point the model client's **`base_url`** at the gateway
  (→ ledger `chat` / `tool_call` rows). Nearly every framework exposes
  this, so the LLM leg is close to universal.
- **MCP leg** — point the framework's MCP client at the gateway's
  **`/mcp/<server>`** route. This requires the client to speak
  **Streamable HTTP**. The gateway does **not** yet bridge
  **stdio**-transport MCP servers.

Governability: 🟢 = both legs clean · 🟡 = LLM clean, MCP partial /
stdio-only / none. Identity (`X-Agent-ID`) is a custom header on both
legs; all 🟢 frameworks support it.

**Tested against the running gateway (LLM + MCP capture verified):**

| Framework | Language / SDK | LLM `base_url` | MCP (HTTP) | Gov. |
| --- | --- | :---: | :---: | :---: |
| LangGraph / LangChain | Python | ✅ | ✅ | 🟢 |
| OpenAI Agents SDK | Python | ✅ | ✅ | 🟢 |
| CrewAI | Python (LiteLLM) | ✅ | ✅ | 🟢 |
| Pydantic AI | Python | ✅ | ✅ | 🟢 |
| LlamaIndex | Python | ✅ | ✅ | 🟢 |
| AutoGen (AgentChat) | Python | ✅ | ✅ | 🟢 |
| Google ADK | Python (LiteLLM) | ✅ | ✅ | 🟢 |
| Strands Agents | Python | ✅ | ✅ | 🟢 |
| Agno | Python | ✅ | ✅ | 🟢 |
| Semantic Kernel | Python | ✅ | ✅ | 🟢 |
| Smolagents | Python | ✅ | ✅ | 🟢 |
| Haystack | Python | ✅ | ✅ | 🟢 |

**Known-compatible by API, not yet tested against the gateway:**

| Framework | Language | LLM base URL | MCP (HTTP) | Gov. |
| --- | --- | :---: | :---: | :---: |
| Vercel AI SDK | TS / JS | ✅ | ✅ | 🟢 |
| Mastra | TS / JS | ✅ | ✅ | 🟢 |
| LangChain.js / LangGraph.js | TS / JS | ✅ | ✅ | 🟢 |
| OpenAI Agents JS | TS / JS | ✅ | ✅ | 🟢 |
| Spring AI | Java | ✅ | ✅ | 🟢 |
| LangChain4j | Java | ✅ | ✅ | 🟢 |
| Genkit | Go / JS | ✅ | 🟡 | 🟡 |
| Eino | Go | ✅ | 🟡 | 🟡 |
| Rig | Rust | ✅ | 🟡 | 🟡 |

The second table reflects framework APIs as of mid-2026; the ecosystem
moves fast, so re-check MCP transport support when bumping versions. A
framework not listed here is very likely still governable on the LLM leg
as long as its model client accepts a custom `base_url`.
