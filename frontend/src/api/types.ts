// Hand-written response types for the endpoints the UI consumes.
// These mirror what dashboard.py returns. Kept narrow (only fields the
// UI actually reads) so type refactors stay focused.
// Regenerate src/api/schema.d.ts via `npm run openapi:sync` if you want
// the full OpenAPI-derived types.

export interface Agent {
  agent_id: string;
  display_name: string | null;
  first_seen: number;
  last_seen: number;
  first_seen_dt: string;
  last_seen_dt: string;
  entry_count: number;
  session_count: number;
}

export interface Me {
  user_id?: string;
  username?: string;
  email?: string;
  roles?: string[];
  must_change_password?: boolean;
}

export interface Stats {
  total: number;
  first_entry: string | null;
  last_entry: string | null;
  unique_agents: number;
  unique_sessions: number;
  activity: Record<string, number>;      // date(YYYY-MM-DD) → count
  agents: Record<string, number>;        // agent_id → count
  action_types: Record<string, number>;  // action_type → count
  upstreams: Record<string, number>;     // provider → count
}

// Fleet & agent trust score — mirrors trust.fleet_trust() in the backend.
// The composite comes from the 5-dimension formula; the dimensions are the
// inputs that produce it.
export type TrustTierKey = "autonomous" | "monitored" | "human_approval" | "isolated";

export interface TrustDimensions {
  security: number;
  compliance: number;
  integrity: number;
  reliability: number;
  economics: number;
}

export interface AgentTrust {
  agent_id: string;
  display_name: string | null;
  score: number;
  tier: string;
  tier_key: TrustTierKey;
  cap_reason: string | null;
  dimensions: TrustDimensions;
  requests: number;
  last_seen: number | null;
}

export interface FleetTrust {
  trust_score: number;
  tier: string;
  tier_key: TrustTierKey;
  active_agents: number;
  dimensions: TrustDimensions;
  tier_counts: Record<TrustTierKey, number>;
  signing_enabled: boolean;
  agents: AgentTrust[];
}

export interface VerificationRun {
  run_id: string;
  run_at: string;            // ISO timestamp
  total_entries: number;
  verified_entries: number;
  chain_breaks: number;
  signature_failures: number;
  first_broken_seq: number | null;
  signature_alg: string;
  status: "pass" | "fail" | string;
  error_sample: string[];
}

export interface Verify {
  valid: boolean;
  entry_count: number;
  chain_breaks: number;
  signature_failures: number;
  errors: string[];
  fingerprint?: string;
}

export interface TokenBucket {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens?: number;
  requests?: number;
}

export interface TokenAnalysis {
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  by_hour: Record<string, TokenBucket>;
  by_agent: Record<string, TokenBucket>;
  by_model: Record<string, TokenBucket>;
  by_upstream: Record<string, TokenBucket>;
}

export type DlpSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface DlpFinding {
  source: string;
  category: string;
  location: [number, number];
  severity: DlpSeverity;
  confidence: number;
  pattern_id: string;
  pattern_name: string;
  matched_value: string;
  redacted_value: string;
  context_snippet: string;
  validator_passed: boolean | null;
  validator_applied: string | null;
}

// BERT findings are whole-text classifications — no span, no pattern.
// Shape comes from dlp-bert /scan and is written by src/kyde/dlp.py.
export interface BertFinding {
  label: string;
  confidence: number;
  action: string;
}

export type DlpStatus = "new" | "in_review" | "escalated" | "closed";

// User-facing dispositions are { false_positive, confirmed_leak }; the
// other two are set only by backend automation (allowlist suppression
// and `dlp dedupe-alerts`) and preserve an honest audit trail. Historical
// rows may carry legacy values (benign_true_positive, policy_violation,
// inconclusive) — the runtime accepts any string from the DB so old
// audit data is not lost; this type just constrains new writes.
export type DlpDisposition =
  | "false_positive"
  | "confirmed_leak"
  | "allowlisted"
  | "duplicate";

export interface DlpAlert {
  id: number | string;
  serial_id?: number | null;
  alert_id?: string;
  agent_id?: string;
  session_id?: string;
  entry_id?: string;
  created_dt: string;
  scanner: string;
  score: number;
  status: string;
  disposition?: DlpDisposition | null;
  disposition_note?: string;
  severity?: string;
  assignee_id?: number | null;
  claimed_at?: number | null;
  closed_at?: number | null;
  reopened_at?: number | null;
  reopen_count?: number;
  linked_incident?: string;
  tags?: string[];
  findings?: DlpFinding[] | string;
  findings_parsed?: DlpFinding[] | string;
  content_redacted?: boolean;
  // MCP-source fields — populated when source_type === "mcp".
  source_type?: "chat" | "mcp";
  mcp_server_id?: string | null;
  mcp_server_name?: string | null;
  mcp_method?: string | null;
  mcp_tool_name?: string | null;
  // True when the request that raised this alert was BLOCKED inline by
  // DLP prevention (vs detect-only alerts from the post-hoc scanner).
  prevented?: boolean;
  [k: string]: unknown;
}

export interface DlpAlertEvent {
  id: number;
  alert_id: string;
  actor_id: number | null;
  actor_kind: "user" | "system" | "rule";
  event_type: "status_change" | "reopen" | "assign" | "comment" | "tag" | "disposition";
  from_status: string | null;
  to_status: string | null;
  from_assignee: number | null;
  to_assignee: number | null;
  disposition: DlpDisposition | null;
  note: string;
  metadata: Record<string, unknown>;
  created_at: number;
}

export interface TransitionInput {
  alert_id: string;
  to_status: DlpStatus;
  disposition?: DlpDisposition;
  assignee_id?: number | null;
  note?: string;
  metadata?: Record<string, unknown>;
}

export interface TpmStatus {
  tpm_available: boolean;
}

export interface EntriesPage {
  items: EntryRow[];
  next_cursor: string | null;
  has_more: boolean;
  total_count?: number;
}

// Derived classifier label persisted by the proxy on every ledger row.
// Distinguishes *why* a chat-shaped entry looks the way it does (e.g.,
// 'chat_tool_only' for assistant-tool-only turns vs.
// 'chat_streaming_partial' for SSE captures that produced no text) AND,
// for Phase B2 fully-logged non-chat rows, *what* endpoint was hit
// (embedding / moderation / models_list / ...). See REQUEST_KIND_* and
// PATH_KIND_* in src/kyde/server.py.
export type RequestKind =
  // Chat-shaped content classifications (path_kind='chat')
  | "chat"
  | "chat_tool_only"
  | "chat_streaming_partial"
  | "chat_empty_request"
  | "chat_empty_content"
  | "policy_block"
  // Non-chat path classifications (Phase B2, path_kind != 'chat'). For
  // these rows action_type='api_call' and the value here doubles as the
  // path kind from agent_traffic_meters.
  | "embedding"
  | "moderation"
  | "models_list"
  | "tokens_count"
  | "audio_transcription"
  | "audio_translation"
  | "audio_speech"
  | "image_generation"
  | "image_edit"
  | "image_variation"
  | "legacy_completion"
  | "file_op"
  | "fine_tuning"
  | "unknown";

// API endpoint bucket recorded per request by the proxy. Independent from
// RequestKind: classifies *what kind of endpoint was hit*, not what the
// payload contained. See server.PATH_KIND_* and agent_traffic_meters.
export type PathKind =
  | "chat"
  | "embedding"
  | "moderation"
  | "models_list"
  | "tokens_count"
  | "audio_transcription"
  | "audio_translation"
  | "audio_speech"
  | "image_generation"
  | "image_edit"
  | "image_variation"
  | "legacy_completion"
  | "file_op"
  | "fine_tuning"
  | "unknown";

export type TrafficMode = "count_only" | "full_logging";

export interface AgentTrafficRow {
  agent_id: string;
  path_kind: PathKind;
  count: number;
  first_seen: string | null;
  last_seen: string | null;
  mode: TrafficMode;
}

export interface EntryRow {
  seq: number;
  dt: string;
  agent_id: string;
  action_type: string;
  model: string;
  upstream: string;
  prompt_tokens: number;
  completion_tokens: number;
  session_id: string;
  tool_count: number;
  first_tool?: string | null;
  entry_id?: string;
  request_kind?: RequestKind;
  // First/last why message excerpt — auditor-only. Empty string for
  // non-auditors or entries with no `why` history.
  why_preview?: string;
}

export interface EntryFacets {
  action_types: string[];
  upstreams: string[];
}

export interface EntryDetail {
  seq: number;
  entry_id: string;
  dt: string;
  agent_id: string;
  model: string;
  action_type: string;
  upstream: string;
  client_ip?: string;
  client_hostname?: string | null;
  session_id?: string;
  user_agent?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_entries?: number;
  signature_valid: boolean;
  content_redacted?: boolean;
  why_parsed?: Array<{ role?: string; content?: unknown }>;
  tool_calls_parsed?: Array<{ function?: string; args?: unknown }>;
  full_messages_parsed?: Array<{ role?: string; content?: unknown }>;
  // Verbatim upstream response body (hashes to output_hash). null on
  // entries recorded before migration 0022 and for non-auditors.
  response_body_parsed?: Record<string, unknown> | null;
  // Assistant reply text extracted server-side (provider-agnostic).
  // "" when the response carried only tool calls or is unavailable.
  assistant_text?: string;
  input_hash?: string;
  output_hash?: string;
  prev_hash?: string;
  entry_hash?: string;
  signature?: string;
  // DLP alerts raised on this entry — populated by the backend so the
  // entry-detail dialog can list them on Metadata and highlight matched
  // messages on Messages. Empty list when there are no alerts.
  dlp_alerts?: DlpAlert[];
  // Index of the first message in `full_messages_parsed` that wasn't
  // already on the prior entry in this session. 0 = first entry, so
  // everything in `full_messages_parsed` is new for this call.
  new_message_offset?: number;
}

export interface SessionsPage {
  items: SessionSummary[];
  next_cursor: string | null;
  has_more: boolean;
}

export type SessionStatus = "blocked" | "observed" | "allowed";

// ── Host resolution (Phase 2)
export type HostStatusFilter = "all" | "labeled" | "unlabeled" | "recently_active";

export interface HostLabelRow {
  ip: string;
  hostname: string | null;
  // "admin" | "dns" | "dns miss" | null — backend stringifies this for
  // the chip in the Settings table so the UI doesn't need to interpret
  // (source, hostname) tuples.
  source: string | null;
  resolved_at: string | null;
  last_seen: number | null;
  last_seen_iso: string | null;
}

export interface HostResolveByIp {
  kind: "ip";
  ip: string;
  hostname: string | null;
  hostname_source: "admin" | "dns" | null;
  ips: string[];
}

export interface HostResolveByName {
  kind: "hostname";
  hostname: string;
  ips: Array<{
    ip: string;
    source: "admin" | "dns";
    last_seen: number | null;
  }>;
}

export type HostResolveResponse = HostResolveByIp | HostResolveByName;

export interface SessionSummary {
  session_id: string;
  serial_id?: number | null;
  entry_count: number;
  agent_count: number;
  first_time: string;
  last_time: string;
  agents?: string[];
  last_timestamp?: number;
  first_timestamp?: number;
  duration_seconds?: number;
  intent?: string | null;
  intent_confidence?: number | null;
  status?: SessionStatus;
  has_block?: boolean;
  has_open_alert?: boolean;
}

export interface SessionDetail {
  session_id?: string;
  serial_id?: number | null;
  content_redacted?: boolean;
  hosts?: Array<{ ip: string; hostname: string | null }>;
  entries: Array<{
    seq: number;
    entry_id?: string;
    dt: string;
    agent_id: string;
    action_type: string;
    model: string;
    why_last?: string;
    tool_count?: number;
    tool_calls?: Array<{ function?: string }>;
    upstream?: string;
    prompt_tokens?: number;
    completion_tokens?: number;
    request_kind?: RequestKind;
    dlp_alerts?: Array<{
      alert_id: string;
      serial_id: number;
      severity?: string | null;
      status?: string | null;
      disposition?: string | null;
      score?: number;
      scanner?: string;
    }>;
  }>;
}

export interface User {
  id: string | number;
  username: string;
  email?: string;
  roles: string[];
  status?: string;
  // Backend returns these as Unix timestamps (float seconds), not strings.
  created_at?: number | string;
  modified_at?: number | string;
  deleted_at?: number | string | null;
  locked?: boolean;
  locked_at?: number | string | null;
  enabled?: boolean;
  deleted?: boolean;
}

export interface CreateUserResponse {
  id: string | number;
  temp_password?: string;
}

export interface ResetPasswordResponse {
  temp_password: string;
}

export interface UpstreamEntry {
  name: string;
  base: string;
  api_prefix: string;
}

export interface Configuration {
  // Edition gating. `edition` is "starter" (free, observe-only) or "enterprise".
  // Starter images physically lack the signing/enforce packages, so the
  // signing-specific fields below are absent — gate UI on these flags.
  edition: "starter" | "enterprise" | string;
  signing_enabled: boolean;
  enforcement_enabled: boolean;
  signing_mode: "tpm" | "software" | "disabled" | string;
  tpm_available: boolean;
  // Present only when signing_enabled (enterprise edition).
  algorithm?: string;
  public_key_fingerprint?: string | null;
  key_paths?: {
    private_key: { path: string; exists: boolean };
    public_key: { path: string; exists: boolean };
    tpm_key: { path: string; exists: boolean };
  };
  configured_upstreams: UpstreamEntry[];
  ledger_backend: string;
  ledger_entry_count: number;
  service_version: string;
}

export interface SettingEntry {
  key: string;
  label: string;
  description: string;
  type: "float" | "int" | "bool" | "string";
  default: string | number | boolean;
  value: string | number | boolean;
  source: "db" | "env" | "default";
  updated_at: number | null;
  updated_by: number | null;
  updated_by_username: string | null;
  // Present only for secret keys (e.g. SMTP_PASSWORD_ENC): the raw value
  // is redacted to "" and this flag indicates whether one is stored.
  is_set?: boolean;
}

export interface SmtpTestResult {
  ok: boolean;
  recipients?: number;
  error?: string;
}

export interface DlpRule {
  id: number;
  kind: "allow" | "block";
  scanner: string | null;
  entity_type: string;
  match_text: string | null;
  note: string;
  hit_count: number;
  last_hit_at: number;
  created_by: number | null;
  created_by_username: string | null;
  created_at: number;
}

// Health of the built-in DLP scanner sidecars (BERT + regex). These ship
// with rules preloaded and cannot be disabled — the only failure mode is
// the sidecar being unreachable, which surfaces here as `ok: false`.
export interface DlpScannerHealth {
  name: "bert" | "regex" | string;
  ok: boolean;
  error: string | null;
  latency_ms: number | null;
}

export interface DlpHealth {
  ok: boolean;
  scanners: DlpScannerHealth[];
}

export interface CreateDlpRuleInput {
  kind: "allow" | "block";
  scanner: string | null;
  entity_type: string;
  match_text: string | null;
  note?: string;
}

export interface ReapplyAllowlistResult {
  scanned: number;
  fully_allowlisted: number;
  partially_updated: number;
  unchanged: number;
}

export interface ServiceMetrics {
  total_entries: number;
  entries_per_hour_24h: number;
  entries_per_hour_1h: number;
  signature_success_rate: number;
  tool_call_ratio: number;
  chain_integrity: { valid: boolean; break_count: number };
  signing_mode: string;
  ledger_size_bytes: number;
  service_start_time: string;
  uptime_seconds: number;
}

// ─── Agent topology ─────────────────────────────────────────────────

export type TopologyWindow = "1h" | "24h" | "7d" | "30d";

export type TopologyLayer = "segment" | "agent" | "gateway" | "upstream" | "model";

export type OriginClass =
  | "public"
  | "rfc1918"
  | "cgnat"
  | "loopback"
  | "link_local"
  | "unique_local_v6"
  | "unknown";

export interface TopologyNode {
  id: string;              // "seg:10.4.0.0/24", "agent:abc123", "tool:cursor", ...
  layer: TopologyLayer;
  label: string;
  meta?: { class?: OriginClass; agent_id?: string };
}

export interface TopologyLink {
  source: string;  // node id
  target: string;  // node id
  value: number;
}

export interface TopologyResponse {
  window: TopologyWindow;
  min_value: number;
  layers?: TopologyLayer[];
  nodes: TopologyNode[];
  links: TopologyLink[];
}

export interface SegmentAgent {
  agent_id: string;
  request_count: number;
  first_seen: number;
  last_seen: number;
  first_seen_iso: string;
  last_seen_iso: string;
  tools: string[];
  upstreams: string[];
}

export interface SegmentIP {
  ip: string;
  request_count: number;
  ua_tool: string;
  // Cached hostname for this IP, when available. Read-only zero-DNS-call
  // join from host_resolutions on the backend; NULL when no fresh row.
  hostname?: string | null;
  hostname_source?: "admin" | "dns" | null;
}

export interface SegmentSession {
  session_id: string;
  request_count: number;
  last_seen: number;
  last_seen_iso: string;
  model: string;
}

export interface TopologySegment {
  subnet: string;
  class: OriginClass;
  window: TopologyWindow;
  agents: SegmentAgent[];
  ips: SegmentIP[];
  sessions: SegmentSession[];
}

export interface CountBreakdown {
  request_count: number;
  // The label column varies per breakdown (`tool`, `upstream`, `model`) —
  // decoded at the consumer via a known key name.
  [label: string]: number | string;
}

export interface TopologyAgentSegment {
  subnet: string;
  class: OriginClass;
  request_count: number;
}

export interface TopologyAgent {
  agent_id: string;
  window: TopologyWindow;
  request_count: number;
  first_seen: number | null;
  first_seen_iso: string | null;
  last_seen: number | null;
  last_seen_iso: string | null;
  segments: TopologyAgentSegment[];
  ips: SegmentIP[];
  tools: Array<CountBreakdown>;
  upstreams: Array<CountBreakdown>;
  models: Array<CountBreakdown>;
  sessions: SegmentSession[];
}

// One Sankey link's drill-down — top agents + recent sessions in that
// specific flow. Powers the Network Map side panel.
export interface TopologyFlow {
  source_layer: string;
  source_label: string;
  target_layer: string;
  target_label: string;
  window: TopologyWindow;
  request_count: number;
  first_seen_iso: string | null;
  last_seen_iso: string | null;
  agents: Array<{
    agent_id: string;
    display_name: string | null;
    request_count: number;
    last_seen_iso: string;
  }>;
  sessions: Array<{
    session_id: string;
    serial_id: number | null;
    request_count: number;
    last_seen_iso: string;
  }>;
}

export interface TopologyIpAgent {
  agent_id: string;
  request_count: number;
  first_seen: number;
  last_seen: number;
  first_seen_iso: string;
  last_seen_iso: string;
  tools: string[];
}

export interface TopologyIp {
  ip: string;
  // Cached/lazy-resolved hostname. NULL means DNS returned nothing or no
  // resolver call has happened yet (the endpoint always triggers one).
  hostname?: string | null;
  // 'admin' (explicit label) or 'dns' (reverse-DNS lookup result).
  hostname_source?: "admin" | "dns" | null;
  class: OriginClass;
  subnet: string;
  window: TopologyWindow;
  request_count: number;
  first_seen: number | null;
  first_seen_iso: string | null;
  last_seen: number | null;
  last_seen_iso: string | null;
  agents: TopologyIpAgent[];
  tools: Array<CountBreakdown>;
  upstreams: Array<CountBreakdown>;
  models: Array<CountBreakdown>;
  sessions: SegmentSession[];
}

// MCP routing — per-tenant routing table. No credential fields: the
// gateway is transparent on upstream auth. Backed by /api/mcp/servers
// (admin-gated CRUD) and surfaced in pages/mcp-servers.tsx.
//
// last_* columns are stamped per-call in mcp_ledger._update_server_health:
// `last_call_at` advances on ok, `last_error_*` populate on transport
// failure or upstream 5xx. 4xx and policy/DLP blocks don't touch them —
// those are caller problems, not upstream problems.
export interface McpServer {
  id: string;
  name: string;
  upstream_url: string;
  enabled: boolean;
  created_at: string | null;
  created_by: number | null;
  last_call_at: string | null;
  last_error_at: string | null;
  last_error_status: number | null;
  last_error_snippet: string | null;
}

export interface McpServersResponse {
  items: McpServer[];
}

// Per-tool policy — one (server, agent_id, tool_name) row in mcp_tool_policies.
// agent_id and tool_name accept the literal '*' wildcard; precedence
// (most-specific-wins) is enforced server-side in mcp_proxy.check_policy.
// Backed by /api/mcp/servers/{name}/policies* (admin-gated).
export interface McpToolPolicy {
  server_id: string;
  agent_id: string;
  tool_name: string;
  decision: "allow" | "deny";
  reason: string | null;
  updated_at: string | null;
  updated_by: number | null;
}

export interface McpToolPoliciesResponse {
  items: McpToolPolicy[];
}

// Aggregator catalog — namespaced union of every enabled server's tools.
// Seeded opportunistically from real `tools/list` traffic and probe-tools
// runs. Backed by /api/mcp/aggregator/catalog (viewer-or-above).
export interface McpAggregatorCatalogItem {
  server_name: string;
  tool: { name: string; [key: string]: unknown };
  age_seconds: number;
}

export interface McpAggregatorCatalog {
  items: McpAggregatorCatalogItem[];
  server_count: number;
  tool_count: number;
  oldest_seconds: number | null;
}

// Admin-action audit log — every CRUD on MCP servers, MCP tool policies
// and DLP policies is recorded here with before/after row snapshots.
// Operational telemetry, separate from the signed evidence chain in the
// ledger. Backed by /api/audit-log (admin-only).
export interface AdminAction {
  id: number;
  actor_id: number | null;
  actor_username: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}

export interface AdminActionsResponse {
  items: AdminAction[];
  total: number;
  limit: number;
  offset: number;
}

// DLP policies — per-pattern enable/disable for the bundled regex set. The
// gateway is the source of truth; toggling pushes the active set to
// dlp-regex. Backed by /api/dlp-policies (admin-gated, no create/delete in v1).
export interface Policy {
  id: string;
  name: string;
  source: string;
  category: string;
  severity: string;
  pattern: string;
  description: string;
  enabled: boolean;
  // Inline prevention opt-in: when the global Policy Prevention setting
  // is on, hits from this pattern BLOCK the request (403) instead of
  // only alerting. Independent of `enabled` (which gates scanning).
  prevention: boolean;
  hits: number;
  last_hit_at: string | null;
}

export interface PolicyListResponse {
  items: Policy[];
}

export interface PolicyResyncResponse {
  loaded: number;
  boot_id: string;
}
