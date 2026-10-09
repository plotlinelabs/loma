"use client";

import { useCallback, useSyncExternalStore } from "react";
import {
  DEFAULT_DICTATION_SHORTCUT,
  DICTATION_SHORTCUT_STORAGE_KEY,
  parseShortcut,
  serializeShortcut,
  type DictationShortcut,
} from "@/lib/dictation-shortcut";

const listeners = new Set<() => void>();
let cachedRaw: string | null | undefined;
let cachedValue: DictationShortcut = DEFAULT_DICTATION_SHORTCUT;

function read(): DictationShortcut {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(DICTATION_SHORTCUT_STORAGE_KEY);
  } catch {
    // localStorage unavailable (private mode): use the default.
  }
  // Keep the snapshot referentially stable for useSyncExternalStore.
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedValue = parseShortcut(raw);
  }
  return cachedValue;
}

function subscribe(notify: () => void) {
  listeners.add(notify);
  const onStorage = (e: StorageEvent) => {
    if (e.key === DICTATION_SHORTCUT_STORAGE_KEY) notify();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(notify);
    window.removeEventListener("storage", onStorage);
  };
}

/** The user's dictation shortcut (null = off) and a setter that updates
 * every mounted composer at once. */
export function useDictationShortcut() {
  const shortcut = useSyncExternalStore(subscribe, read, () => DEFAULT_DICTATION_SHORTCUT);
  const setShortcut = useCallback((next: DictationShortcut) => {
    try {
      window.localStorage.setItem(DICTATION_SHORTCUT_STORAGE_KEY, serializeShortcut(next));
    } catch {
      // Private mode: the change lasts for this tab only.
    }
    listeners.forEach((notify) => notify());
  }, []);
  return [shortcut, setShortcut] as const;
}
