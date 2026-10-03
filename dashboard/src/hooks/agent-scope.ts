import type { AgentIdentity } from "@/lib/agents-api";
import type { AvailableSkill, ToolConfig } from "@/lib/api";

export interface AgentScopeItem {
  id: string;
  name: string;
  description: string;
}
type ScopedAgent = Pick<AgentIdentity, "tools" | "skills">;

// Sent while an agent is selected: the agent's own scope is the only limit, and
// explicit nulls stop the backend falling back to a conversation's saved picks.
export const AGENT_TOOL_CONFIG: ToolConfig = {
  enabled_skills: null,
  enabled_tools: null,
};

const plural = (count: number, noun: string) =>
  `${count} ${noun}${count === 1 ? "" : "s"}`;

/** Chip label. An agent with nothing set for a domain is unrestricted there. */
export function agentScopeSummary(agent: ScopedAgent): string {
  const tools = agent.tools?.length || 0;
  const skills = agent.skills?.length || 0;
  if (!tools && !skills) return "All tools & skills";
  return `${tools ? plural(tools, "tool") : "All tools"} · ${skills ? plural(skills, "skill") : "All skills"}`;
}

/** Agents store personal tools by CLI name and integrations by display name. */
export function agentScopeTools(
  agent: ScopedAgent,
  labels: Record<string, string>,
): AgentScopeItem[] {
  return (agent.tools || []).map((id) => ({
    id,
    name: labels[id] || id,
    description: "",
  }));
}

/** Falls back to the slug until the catalog loads, or for skills the user cannot see. */
export function agentScopeSkills(
  agent: ScopedAgent,
  catalog: AvailableSkill[],
): AgentScopeItem[] {
  return (agent.skills || []).map((slug) => {
    const skill = catalog.find((s) => s.slug === slug);
    return {
      id: slug,
      name: skill?.name || slug,
      description: skill?.description || "",
    };
  });
}
