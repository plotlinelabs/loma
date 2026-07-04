"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import { fetchNeedsInputCount } from "./api";

const POLL_INTERVAL_MS = 5000;

interface TaskAttentionValue {
  /** Number of the user's board tasks currently waiting on their input. */
  needsInputCount: number;
  refresh: () => void;
}

const TaskAttentionContext = createContext<TaskAttentionValue>({
  needsInputCount: 0,
  refresh: () => {},
});

export function useTaskAttention() {
  return useContext(TaskAttentionContext);
}

/** Strip any previous "(n) " prefix so counts never stack. */
function baseTitle(title: string): string {
  return title.replace(/^\(\d+\)\s*/, "");
}

/**
 * Short two-note ping via WebAudio — no asset to ship. The AudioContext is
 * created lazily on the first user gesture (autoplay policy); if it's still
 * locked we skip silently.
 */
function createPinger() {
  let ctx: AudioContext | null = null;

  const ensureContext = () => {
    if (typeof window === "undefined" || !("AudioContext" in window)) return;
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
  };

  const play = () => {
    if (!ctx || ctx.state !== "running") return;
    const now = ctx.currentTime;
    for (const [freq, at] of [[880, 0], [1174.66, 0.09]] as const) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + at);
      gain.gain.exponentialRampToValueAtTime(0.06, now + at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.18);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + at);
      osc.stop(now + at + 0.2);
    }
  };

  return { ensureContext, play };
}

export function TaskAttentionProvider({ children }: { children: React.ReactNode }) {
  const { status } = useSession();
  const pathname = usePathname();
  const [needsInputCount, setNeedsInputCount] = useState(0);
  const prevCountRef = useRef<number | null>(null);
  const pingerRef = useRef<ReturnType<typeof createPinger> | null>(null);

  // Unlock audio on the first user gesture.
  useEffect(() => {
    pingerRef.current ??= createPinger();
    const unlock = () => pingerRef.current?.ensureContext();
    window.addEventListener("pointerdown", unlock, { once: true });
    return () => window.removeEventListener("pointerdown", unlock);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const count = await fetchNeedsInputCount();
      const prev = prevCountRef.current;
      prevCountRef.current = count;
      setNeedsInputCount(count);
      // Ding only on transitions into needing input, never on first load,
      // and only in the visible tab — push notifications cover the rest.
      if (prev !== null && count > prev && document.visibilityState === "visible") {
        pingerRef.current?.play();
      }
    } catch {
      // Transient poll failures keep the last known count.
    }
  }, []);

  useEffect(() => {
    if (status !== "authenticated") return;
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [status, refresh]);

  // Tab title "(n) " prefix — re-applied on navigation since Next resets
  // titles (the delayed pass runs after Next's own metadata update).
  useEffect(() => {
    const apply = () => {
      const title = baseTitle(document.title);
      document.title = needsInputCount > 0 ? `(${needsInputCount}) ${title}` : title;
    };
    apply();
    const timer = setTimeout(apply, 500);
    return () => clearTimeout(timer);
  }, [needsInputCount, pathname]);

  return (
    <TaskAttentionContext.Provider value={{ needsInputCount, refresh }}>
      {children}
    </TaskAttentionContext.Provider>
  );
}
