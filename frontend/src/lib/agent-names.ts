export interface AgentLike {
  id: string;
  display_name?: string | null;
  primary_tool?: string | null;
}

export function getAgentDisplayName(agentIdOrObj: string | AgentLike): string {
  if (typeof agentIdOrObj === 'string') {
    const clean = agentIdOrObj.replace(/^agent:/, '');
    const shortHash = clean.slice(0, 8);
    return `Claude Code Agent (${shortHash})`;
  }
  if (agentIdOrObj.display_name) return agentIdOrObj.display_name;
  const clean = agentIdOrObj.id.replace(/^agent:/, '');
  const shortHash = clean.slice(0, 8);
  const tool = agentIdOrObj.primary_tool ?? 'Claude Code';
  return `${tool} Agent (${shortHash})`;
}

export function getAgentShortName(agentIdOrObj: string | AgentLike): string {
  const full = getAgentDisplayName(agentIdOrObj);
  return full.length > 32 ? full.slice(0, 30) + '…' : full;
}
