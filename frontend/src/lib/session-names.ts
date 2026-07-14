const INTENT_KEYWORDS: [string, string][] = [
  ['crm', 'CRM Query'],
  ['refactor', 'Code Refactor'],
  ['debug', 'Debug Session'],
  ['config', 'Config Change'],
  ['email', 'Email Task'],
  ['code', 'Code Session'],
  ['query', 'Data Query'],
  ['search', 'Search Task'],
];

// LLM intent labels (from intent_classifier.py) → human-readable names. When
// the backend has a cached classification, it lands here instead of the
// keyword fallback.
const LLM_INTENT_LABELS: Record<string, string> = {
  data_query: 'Data Query',
  code_generation: 'Code Generation',
  code_review: 'Code Review',
  research: 'Research',
  summarization: 'Summarization',
  debugging: 'Debugging',
  configuration: 'Configuration',
  other: 'Untitled Session',
};

export function classifyIntent(text: string): string {
  const lower = text.toLowerCase();
  for (const [kw, label] of INTENT_KEYWORDS) {
    if (lower.includes(kw)) return label;
  }
  return 'Untitled Session';
}

export function getSessionDisplayName(session: {
  session_id: string;
  first_message?: string | null;
  agent_id?: string | null;
  first_time?: string;
  intent?: string | null;
}): string {
  const agentShort = session.agent_id
    ? session.agent_id.replace(/^agent:/, '').slice(0, 8)
    : 'Unknown Agent';
  // Prefer the backend LLM classification when present; fall back to the
  // keyword classifier when no row exists yet in session_intents.
  const intent = session.intent
    ? LLM_INTENT_LABELS[session.intent] ?? session.intent
    : classifyIntent(session.first_message ?? session.session_id ?? '');
  if (session.first_time) {
    const d = new Date(session.first_time);
    const formatted = d.toLocaleDateString('de-DE', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    });
    return `${agentShort} · ${intent} · ${formatted}`;
  }
  return `${agentShort} · ${intent}`;
}
