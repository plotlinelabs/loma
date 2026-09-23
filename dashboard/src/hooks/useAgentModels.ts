"use client";

import { useEffect, useState } from "react";
import { fetchAgentModels, type AgentModel } from "@/lib/api";

const MODEL_STORAGE_KEY = "dashboard-chat-selected-model";

export const FAVORITE_MODEL_IDS = [
  "anthropic/claude-opus-5-5",
  "codex/gpt-6-sol",
  "codex/gpt-6-astra",
] as const;

export const FAVORITE_MODEL_LABELS: Record<string, string> = {
  "anthropic/claude-opus-5-5": "Claude-Opus-5.5 (For Coding)",
  "codex/gpt-6-sol": "GPT-6-Sol (For Writing)",
  "codex/gpt-6-astra": "GPT-6-Astra (For Complex Tasks)",
};

export function favoriteModelRank(model: AgentModel): number | null {
  const index = FAVORITE_MODEL_IDS.indexOf(model.id as typeof FAVORITE_MODEL_IDS[number]);
  return index === -1 ? null : index;
}

export function isFavoriteModel(model: AgentModel): boolean {
  return favoriteModelRank(model) !== null;
}

export type ModelLoadState = "loading" | "ready" | "error";

/**
 * Agent-model catalog + selection, shared by the chat composer and the tasks
 * quick-add composer. Selection priority: explicit initialModel (e.g. a board
 * saved preference > explicit initialModel (e.g. a board task's chosen
 * model) > backend default. Selecting a
 * model persists it as the saved preference.
 */
export function useAgentModels(initialModel?: string) {
  const [models, setModels] = useState<AgentModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [loadState, setLoadState] = useState<ModelLoadState>("loading");

  useEffect(() => {
    let cancelled = false;

    async function loadModels() {
      setLoadState("loading");
      try {
        const catalog = await fetchAgentModels();
        if (cancelled) return;
        const list = catalog.models || [];
        setModels(list);

        const saved = typeof window !== "undefined"
          ? window.localStorage.getItem(MODEL_STORAGE_KEY)
          : null;
        const savedIsValid = saved && list.some((model) => model.id === saved);
        const initialIsValid = initialModel && list.some((model) => model.id === initialModel);
        const nextModel = savedIsValid
          ? saved
          : initialIsValid
            ? initialModel
            : catalog.default_model || list[0]?.id || "";
        setSelectedModel(nextModel);
        setLoadState("ready");
      } catch (e) {
        if (cancelled) return;
        console.warn("Failed to load agent models", e);
        setModels([]);
        setSelectedModel("");
        setLoadState("error");
      }
    }

    loadModels();
    return () => {
      cancelled = true;
    };
  }, [initialModel]);

  const selectModel = (value: string) => {
    setSelectedModel(value);
    try {
      window.localStorage.setItem(MODEL_STORAGE_KEY, value);
    } catch {}
  };

  return { models, selectedModel, selectModel, loadState };
}
