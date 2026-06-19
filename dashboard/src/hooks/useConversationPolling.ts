import { useEffect, useRef, useCallback, useState } from "react";
import { fetchConversation } from "../lib/api";
import type { Conversation, Turn } from "../lib/api";

/** Fast polling while the conversation is actively running. */
const ACTIVE_POLLING_INTERVAL_MS = 3000;
/** Slower background polling to detect new activity on completed conversations. */
const IDLE_POLLING_INTERVAL_MS = 10000;

interface UseConversationPollingOptions {
  /** The conversation ID to poll. */
  conversationId: string;
  /** Called whenever fresh data is fetched. */
  onData: (conversation: Conversation, turns: Turn[]) => void;
  /** Called when a fetch error occurs. */
  onError?: (error: Error) => void;
}

/**
 * Polls the conversation detail endpoint. Uses a fast interval while the
 * conversation status is "running" and falls back to a slower background
 * interval when the conversation is "completed" or "error" so that new
 * activity (e.g. a follow-up Slack message in the same thread) is picked up
 * automatically.
 */
export function useConversationPolling({
  conversationId,
  onData,
  onError,
}: UseConversationPollingOptions) {
  const [isPolling, setIsPolling] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onDataRef = useRef(onData);
  const onErrorRef = useRef(onError);
  /** Track the current interval speed so we can switch between active/idle. */
  const currentIntervalMs = useRef<number | null>(null);

  // Keep callback refs up to date without restarting the interval.
  useEffect(() => {
    onDataRef.current = onData;
  }, [onData]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const stopPolling = useCallback(() => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    currentIntervalMs.current = null;
    setIsPolling(false);
  }, []);

  const startPollingWithInterval = useCallback(
    (intervalMs: number) => {
      // If already polling at the requested speed, no-op.
      if (intervalRef.current !== null && currentIntervalMs.current === intervalMs) return;

      // Clear any existing interval before starting a new one.
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }

      currentIntervalMs.current = intervalMs;
      setIsPolling(true);

      intervalRef.current = setInterval(async () => {
        try {
          const data = await fetchConversation(conversationId);
          onDataRef.current(data.conversation, data.turns);

          // Switch between fast and slow polling based on status.
          if (data.conversation.status === "running") {
            // Conversation is active — ensure we're on the fast interval.
            if (currentIntervalMs.current !== ACTIVE_POLLING_INTERVAL_MS) {
              startPollingWithInterval(ACTIVE_POLLING_INTERVAL_MS);
            }
          } else {
            // Conversation is idle — drop to slow background polling.
            if (currentIntervalMs.current !== IDLE_POLLING_INTERVAL_MS) {
              startPollingWithInterval(IDLE_POLLING_INTERVAL_MS);
            }
          }
        } catch (err) {
          onErrorRef.current?.(err instanceof Error ? err : new Error(String(err)));
        }
      }, intervalMs);
    },
    [conversationId],
  );

  /** Public helper: start polling (picks the right speed automatically). */
  const startPolling = useCallback(
    (status?: string) => {
      const interval =
        status === "running" ? ACTIVE_POLLING_INTERVAL_MS : IDLE_POLLING_INTERVAL_MS;
      startPollingWithInterval(interval);
    },
    [startPollingWithInterval],
  );

  // Clean up on unmount.
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  return { isPolling, startPolling, stopPolling };
}
