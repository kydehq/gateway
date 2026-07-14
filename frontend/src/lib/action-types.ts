export const ACTION_TYPES = [
  'chat', 'tool_call', 'tool_result', 'setting_change',
  'auth', 'policy_change', 'policy_block', 'agent_register',
] as const;

export type ActionType = typeof ACTION_TYPES[number];

// Editorial Mono: action kinds are not severities. Keep them neutral, with
// blue (the single brand accent) for auth/registration and red reserved for
// a true policy block. No amber, no dark: variants.
export const ACTION_TYPE_COLOR: Record<ActionType, string> = {
  chat:           'text-foreground',
  tool_call:      'text-muted-foreground',
  tool_result:    'text-muted-foreground',
  setting_change: 'text-foreground',
  auth:           'text-primary',
  policy_change:  'text-foreground',
  policy_block:   'text-destructive',
  agent_register: 'text-primary',
};

export const ACTION_TYPE_BG: Record<ActionType, string> = {
  chat:           'bg-muted/40',
  tool_call:      'bg-muted/40',
  tool_result:    'bg-muted/40',
  setting_change: 'bg-muted/40',
  auth:           'bg-primary/10',
  policy_change:  'bg-muted/40',
  policy_block:   'bg-destructive/10',
  agent_register: 'bg-primary/10',
};

export const ACTION_TYPE_LABEL: Record<ActionType, string> = {
  chat:           'Chat',
  tool_call:      'Tool Call',
  tool_result:    'Tool Result',
  setting_change: 'Setting Change',
  auth:           'Auth',
  policy_change:  'Policy Change',
  policy_block:   'Policy Block',
  agent_register: 'Agent Register',
};
