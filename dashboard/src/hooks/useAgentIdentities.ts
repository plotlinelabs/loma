"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchAgentIdentities, type AgentIdentity } from "@/lib/agents-api";

const AGENT_STORAGE_KEY = "dashboard-chat-selected-agent";

export type AgentLoadState = "loading" | "ready" | "error";

/**
 * Agent identity catalog + selection for chat composers. `null` selection means
 * the default Loma agent (current global behavior).
 *
 * initialAgentId semantics: `undefined` = a brand-new chat (seed from the saved
 * preference); a string = the conversation's pinned agent (always wins); `null`
 * = an existing conversation with no pinned agent (stay on the default — never
 * retroactively pin the saved preference onto an old conversation).
 */
export function useAgentIdentities(initialAgentId?: string | null) {
  const [agents, setAgents] = useState<AgentIdentity[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<AgentLoadState>("loading");

  useEffect(() => {
    let cancelled = false;

    async function loadAgents() {
      setLoadState("loading");
      try {
        const data = await fetchAgentIdentities();
        if (cancelled) return;
        const list = (data.agents || []).filter((a) => a.status !== "disabled");
        setAgents(list);

        const saved = typeof window !== "undefined"
          ? window.localStorage.getItem(AGENT_STORAGE_KEY)
          : null;
        const initialIsValid = initialAgentId && list.some((a) => a.agent_id === initialAgentId);
        const savedIsValid = saved && list.some((a) => a.agent_id === saved);
        setSelectedAgentId(
          initialIsValid
            ? initialAgentId!
            : initialAgentId !== undefined
              ? null
              : savedIsValid
                ? saved
                : null,
        );
        setLoadState("ready");
      } catch (e) {
        if (cancelled) return;
        console.warn("Failed to load agent identities", e);
        setAgents([]);
        setSelectedAgentId(null);
        setLoadState("error");
      }
    }

    loadAgents();
    return () => {
      cancelled = true;
    };
  }, [initialAgentId]);

  const selectAgent = (agentId: string | null) => {
    setSelectedAgentId(agentId);
    try {
      if (agentId) window.localStorage.setItem(AGENT_STORAGE_KEY, agentId);
      else window.localStorage.removeItem(AGENT_STORAGE_KEY);
    } catch {}
  };

  const selectedAgent = useMemo(
    () => agents.find((a) => a.agent_id === selectedAgentId) || null,
    [agents, selectedAgentId],
  );

  return { agents, selectedAgent, selectedAgentId, selectAgent, loadState };
}
