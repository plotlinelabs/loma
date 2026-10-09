"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createTask, createVoiceSession, fetchConversation, fetchTasksBoard, fetchVoiceStatus,
  interruptAgent, sendTaskMessage, type Task, type ToolConfig,
} from "@/lib/api";
import {
  appendTranscript, matchTasks, parseToolCall, taskLabel, voiceColumn,
  type VoiceLine, type VoiceToolCall,
} from "@/lib/voice-dispatcher";

export type VoiceState = "idle" | "connecting" | "listening" | "working" | "speaking";

export interface VoiceAction {
  id: string;
  label: string;
  ok: boolean;
  /** Task the action touched; the panel links to it. */
  conversationId?: string;
}

/** Live audio is billed per second, so a forgotten session ends itself. */
const IDLE_LIMIT_MS = 3 * 60 * 1000;
const SESSION_LIMIT_MS = 20 * 60 * 1000;
const CLOSE_TIMEOUT_MS = 5000;
const ICE_TIMEOUT_MS = 10_000;
/** Captions arrive in bursts; hold "speaking" briefly between them. */
const SPEAKING_HOLD_MS = 1500;
const LIST_LIMIT = 30;
const REPLY_LIMIT = 1500;

type ToolResult = Record<string, unknown>;

/** Whether to offer voice mode: the backend has a key and the browser can do WebRTC. */
export function useVoiceAvailable(): boolean {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    if (typeof RTCPeerConnection === "undefined" || !navigator.mediaDevices?.getUserMedia) return;
    let cancelled = false;
    fetchVoiceStatus().then((s) => { if (!cancelled) setAvailable(s.enabled); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  return available;
}

/** Voice dispatcher for the tasks board: a live WebRTC session with the
 * voice model, whose delegated tool calls run here against the normal task
 * routes. Voice only hands out, reads, steers and stops tasks. */
export function useVoiceDispatcher({ boardId, model, toolConfig, onBoardChanged }: {
  boardId?: string;
  model?: string;
  toolConfig?: ToolConfig;
  /** Called after voice changes the board so it refreshes right away. */
  onBoardChanged: () => void;
}) {
  const [state, setState] = useState<VoiceState>("idle");
  const [lines, setLines] = useState<VoiceLine[]>([]);
  const [actions, setActions] = useState<VoiceAction[]>([]);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const peerRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tool calls of the backend response in flight; answered together when it completes.
  const pendingRef = useRef<Array<{ callId: string; result: Promise<ToolResult> }>>([]);
  const busyRef = useRef(0);
  const seenCallsRef = useRef(new Set<string>());
  const generationRef = useRef(0);
  const settingsRef = useRef({ model, toolConfig });
  settingsRef.current = { model, toolConfig };
  const speakingUntilRef = useRef(0);
  const lastActivityRef = useRef(0);
  const startedAtRef = useRef(0);
  const readyRef = useRef(false);
  const boardIdRef = useRef(boardId);
  boardIdRef.current = boardId;
  const onBoardChangedRef = useRef(onBoardChanged);
  onBoardChangedRef.current = onBoardChanged;

  const cleanup = useCallback((message?: string) => {
    generationRef.current += 1;
    if (timerRef.current) clearInterval(timerRef.current);
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    timerRef.current = null;
    closeTimerRef.current = null;
    micRef.current?.getTracks().forEach((t) => t.stop());
    micRef.current = null;
    channelRef.current?.close();
    channelRef.current = null;
    peerRef.current?.close();
    peerRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.srcObject = null;
    }
    audioRef.current = null;
    pendingRef.current = [];
    seenCallsRef.current.clear();
    busyRef.current = 0;
    readyRef.current = false;
    setMuted(false);
    setState("idle");
    if (message) setError(message);
  }, []);

  // Leaving the board ends the session: no hidden open mic, no idle billing.
  useEffect(() => () => cleanup(), [cleanup, boardId]);

  const send = useCallback((event: Record<string, unknown>) => {
    const channel = channelRef.current;
    if (channel?.readyState === "open") channel.send(JSON.stringify(event));
  }, []);

  const stop = useCallback((message?: string) => {
    if (!peerRef.current) return;
    if (message) setError(message);
    if (!readyRef.current || closeTimerRef.current) {
      cleanup();
      return;
    }
    // Ask for a clean close so usage is finalized; the session.closed handler cleans up.
    micRef.current?.getTracks().forEach((t) => { t.enabled = false; });
    send({ type: "session.close" });
    closeTimerRef.current = setTimeout(() => cleanup(), CLOSE_TIMEOUT_MS);
  }, [cleanup, send]);

  const logAction = useCallback((label: string, ok: boolean, conversationId?: string) => {
    setActions((prev) => [...prev, { id: `${Date.now()}-${prev.length}`, label, ok, conversationId }].slice(-20));
  }, []);

  const runTool = useCallback(async (call: VoiceToolCall): Promise<ToolResult> => {
    const text = (key: string) => (typeof call.args[key] === "string" ? (call.args[key] as string).trim() : "");
    const loadTasks = async () => (await fetchTasksBoard("", boardIdRef.current)).tasks;
    // One task for a spoken reference, or the tool result explaining why not.
    const resolve = async (): Promise<{ task: Task } | { result: ToolResult }> => {
      const ref = text("task");
      if (!ref) return { result: { error: "Say which task." } };
      const found = matchTasks(await loadTasks(), ref);
      if (found.length === 1) return { task: found[0] };
      if (found.length === 0) return { result: { error: `No task matches "${ref}".` } };
      return { result: { ambiguous: true, candidates: found.slice(0, 5).map(taskLabel) } };
    };

    try {
      switch (call.name) {
        case "list_tasks": {
          const column = text("column") || "all";
          const tasks = (await loadTasks())
            .map((t) => ({ id: t.conversation_id, title: taskLabel(t), column: voiceColumn(t.column) }))
            .filter((t) => (column === "all" ? t.column !== "done" : t.column === column));
          return { total: tasks.length, tasks: tasks.slice(0, LIST_LIMIT) };
        }
        case "create_task": {
          const prompt = text("prompt");
          if (!prompt) return { error: "The task needs instructions." };
          const start = call.args.start !== false;
          const { task } = await createTask({
            prompt, start, board: boardIdRef.current,
            model: settingsRef.current.model, tool_config: settingsRef.current.toolConfig,
          });
          logAction(`${start ? "Started" : "Saved draft"}: ${taskLabel(task)}`, true, task.conversation_id);
          onBoardChangedRef.current();
          return { ok: true, id: task.conversation_id, title: taskLabel(task), state: start ? "running" : "draft" };
        }
        case "get_task_status": {
          const found = await resolve();
          if ("result" in found) return found.result;
          const { conversation } = await fetchConversation(found.task.conversation_id);
          logAction(`Checked: ${taskLabel(found.task)}`, true, found.task.conversation_id);
          return {
            title: taskLabel(found.task),
            column: voiceColumn(found.task.column),
            run_status: conversation.status || "not started",
            latest_reply: (conversation.final_response || "").slice(-REPLY_LIMIT) || null,
          };
        }
        case "steer_task": {
          const message = text("message");
          if (!message) return { error: "The message is empty." };
          const found = await resolve();
          if ("result" in found) return found.result;
          const outcome = await sendTaskMessage(found.task.conversation_id, message);
          const delivered = outcome !== "busy";
          logAction(`${delivered ? "Messaged" : "Busy, not sent"}: ${taskLabel(found.task)}`, delivered, found.task.conversation_id);
          onBoardChangedRef.current();
          return delivered
            ? { ok: true, title: taskLabel(found.task), delivery: outcome }
            : { error: "The task is busy and can't take a message until its run ends." };
        }
        case "stop_task": {
          const found = await resolve();
          if ("result" in found) return found.result;
          if (voiceColumn(found.task.column) !== "working") return { error: "That task is not running." };
          const result = await interruptAgent(found.task.conversation_id);
          if (!result.interrupted) return { error: "The task did not confirm it was stopped. Refresh its status." };
          logAction(`Stopped: ${taskLabel(found.task)}`, true, found.task.conversation_id);
          onBoardChangedRef.current();
          return { ok: true, title: taskLabel(found.task), stopped: true };
        }
        default:
          return { error: `Unknown tool ${call.name}` };
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : "The action failed";
      logAction(message, false);
      return { error: message };
    }
  }, [logAction]);

  const handleEvent = useCallback((event: { type?: string; [key: string]: unknown }) => {
    // Usage ticks arrive on a timer; only talk and task work count as activity.
    if (event.type !== "session.usage.updated") lastActivityRef.current = Date.now();
    switch (event.type) {
      case "session.started":
        readyRef.current = true;
        setState("listening");
        return;
      case "session.input_transcript.delta":
      case "session.output_transcript.delta": {
        const speaker = event.type === "session.input_transcript.delta" ? "user" : "assistant";
        if (speaker === "assistant") speakingUntilRef.current = Date.now() + SPEAKING_HOLD_MS;
        setLines((prev) => appendTranscript(
          prev, speaker, String(event.delta ?? ""), Number(event.start_ms ?? 0), Number(event.end_ms ?? 0)));
        return;
      }
      case "session.delegation.created":
        busyRef.current = 1;
        return;
      case "response.event": {
        const call = parseToolCall(event);
        if (call) {
          if (seenCallsRef.current.has(call.callId)) return;
          seenCallsRef.current.add(call.callId);
          pendingRef.current.push({ callId: call.callId, result: runTool(call) });
          return;
        }
        const nested = (event.event as { type?: string } | undefined)?.type;
        if (nested !== "response.completed" && nested !== "response.failed" && nested !== "response.incomplete") return;
        const pending = pendingRef.current;
        pendingRef.current = [];
        if (nested !== "response.completed" || pending.length === 0) {
          // The backend's turn is over: no tool results are owed.
          busyRef.current = Math.max(0, busyRef.current - 1);
          return;
        }
        // Every call of the response gets its result, then the backend continues.
        const generation = generationRef.current;
        void Promise.all(pending.map(async ({ callId, result }) => ({ callId, output: await result })))
          .then((results) => {
            if (generation !== generationRef.current) return;
            for (const { callId, output } of results) {
              send({
                type: "response.item.create",
                item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) },
              });
            }
            send({ type: "response.create" });
          });
        return;
      }
      case "error": {
        const detail = event.error as { message?: string } | undefined;
        console.warn("Voice session error", detail);
        if (detail?.message) setError(detail.message);
        return;
      }
      case "session.closed":
        cleanup();
        return;
    }
  }, [cleanup, runTool, send]);

  const start = useCallback(async () => {
    if (peerRef.current) return;
    setError(null);
    setLines([]);
    setActions([]);
    setState("connecting");
    const peer = new RTCPeerConnection();
    peerRef.current = peer;
    try {
      const audio = new Audio();
      audio.autoplay = true;
      peer.addEventListener("connectionstatechange", () => {
        if (peerRef.current === peer && peer.connectionState === "failed") cleanup("Voice connection failed.");
      });
      audioRef.current = audio;
      peer.addEventListener("track", (e) => {
        audio.srcObject = new MediaStream([e.track]);
        audio.play().catch(() => setError("Tap the page to hear Loma."));
      });
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Ended (or left the board) while the permission prompt was open.
      if (peerRef.current !== peer) {
        mic.getTracks().forEach((t) => t.stop());
        return;
      }
      micRef.current = mic;
      for (const track of mic.getAudioTracks()) peer.addTrack(track, mic);

      // The event channel must exist before the offer is created.
      const channel = peer.createDataChannel("oai-events");
      channelRef.current = channel;
      channel.addEventListener("message", ({ data }) => {
        if (peerRef.current !== peer) return;
        try {
          const event = JSON.parse(data);
          if (!closeTimerRef.current || event.type === "session.closed") handleEvent(event);
        } catch (e) {
          console.warn("Unreadable voice event", e);
        }
      });
      channel.addEventListener("close", () => {
        if (peerRef.current === peer) cleanup(readyRef.current && !closeTimerRef.current ? "Voice disconnected." : undefined);
      });

      await peer.setLocalDescription(await peer.createOffer());
      if (peer.iceGatheringState !== "complete") {
        await new Promise<void>((resolve) => {
          const done = () => {
            if (peer.iceGatheringState !== "complete") return;
            clearTimeout(timeout);
            peer.removeEventListener("icegatheringstatechange", done);
            resolve();
          };
          // Offer whatever candidates we have rather than fail on a slow network.
          const timeout = setTimeout(() => {
            peer.removeEventListener("icegatheringstatechange", done);
            resolve();
          }, ICE_TIMEOUT_MS);
          peer.addEventListener("icegatheringstatechange", done);
        });
      }
      if (peerRef.current !== peer) return;
      const offer = peer.localDescription?.sdp;
      if (!offer) throw new Error("Could not create the voice connection");
      const answer = await createVoiceSession(offer);
      if (peerRef.current !== peer) return;
      await peer.setRemoteDescription({ type: "answer", sdp: answer.sdp });

      startedAtRef.current = lastActivityRef.current = Date.now();
      timerRef.current = setInterval(() => {
        const now = Date.now();
        if (now - startedAtRef.current > SESSION_LIMIT_MS) return stop("Voice ended after 20 minutes.");
        if (now - lastActivityRef.current > IDLE_LIMIT_MS && busyRef.current === 0) return stop("Voice ended after 3 quiet minutes.");
        if (!readyRef.current) {
          if (now - startedAtRef.current > 30_000) stop("Voice connection timed out. Please try again.");
          return;
        }
        setState(now < speakingUntilRef.current ? "speaking" : busyRef.current > 0 ? "working" : "listening");
      }, 300);
    } catch (e) {
      if (peerRef.current !== peer) return;
      const denied = e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "NotFoundError");
      cleanup(denied ? "Microphone access is needed for voice mode." : e instanceof Error ? e.message : "Could not start voice mode");
    }
  }, [cleanup, handleEvent, stop]);

  const toggleMute = useCallback(() => {
    setMuted((prev) => {
      micRef.current?.getAudioTracks().forEach((t) => { t.enabled = prev; });
      return !prev;
    });
  }, []);

  const sendText = useCallback((text: string) => {
    if (!readyRef.current || channelRef.current?.readyState !== "open") {
      throw new Error("Wait for voice to connect, or end voice to create a task normally.");
    }
    if (busyRef.current > 0 || pendingRef.current.length) {
      throw new Error("Loma is handling your last request. Please try again in a moment.");
    }
    lastActivityRef.current = Date.now();
    setLines((prev) => [...prev, { speaker: "user" as const, text, endMs: 0 }].slice(-40));
    send({ type: "response.item.create", item: {
      type: "message", role: "user", content: [{ type: "input_text", text }],
    } });
    busyRef.current = 1;
    send({ type: "response.create" });
  }, [send]);

  return { sendText, state, active: state !== "idle", lines, actions, muted, error, start, stop: () => stop(), toggleMute };
}
