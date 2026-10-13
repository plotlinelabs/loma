"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { fetchPromptTemplates, type PromptTemplate } from "@/lib/api";

// One shared list for every composer and the Templates dialog.
let templates: PromptTemplate[] = [];
let loaded = false;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((notify) => notify());
}

async function load(force = false) {
  if (inflight) return inflight;
  if (loaded && !force) return;
  inflight = fetchPromptTemplates()
    .then((list) => {
      templates = list;
      loaded = true;
      emit();
    })
    .catch(() => {
      // Slash commands are an add-on; the composer works without them.
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

function subscribe(notify: () => void) {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

/** Slash-command templates: built-ins first, then the user's own. */
export function usePromptTemplates() {
  const list = useSyncExternalStore(subscribe, () => templates, () => templates);
  useEffect(() => {
    void load();
  }, []);
  const reload = useCallback(() => load(true), []);
  return { templates: list, reload };
}

/** The template a message invokes (`/name ...`), if any. */
export function matchCommand(list: PromptTemplate[], text: string): PromptTemplate | undefined {
  const match = text.match(/^\/([a-z0-9][a-z0-9-]*)(\s|$)/);
  return match ? list.find((t) => t.name === match[1]) : undefined;
}
