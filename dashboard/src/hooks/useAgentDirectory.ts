"use client";

import { useEffect, useState } from "react";
import { fetchAgentIdentities, type AgentIdentity } from "@/lib/agents-api";

/**
 * Agents the viewer can see, keyed by agent_id: used to put an avatar and role
 * next to the agent name on attributed replies. Read-only: unlike
 * useAgentIdentities it never touches the composer's saved selection.
 */
export function useAgentDirectory(): Record<string, AgentIdentity> {
  const [agents, setAgents] = useState<Record<string, AgentIdentity>>({});
  useEffect(() => {
    let cancelled = false;
    fetchAgentIdentities()
      .then((data) => {
        if (cancelled) return;
        setAgents(Object.fromEntries((data.agents || []).map((a) => [a.agent_id, a])));
      })
      .catch(() => {
        /* names still come from the message itself */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return agents;
}
