"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createTask, createVoiceSession, fetchConversation, fetchTasksBoard, fetchVoiceStatus,
  interruptAgent, sendTaskMessage, updateTask, updateTaskStar, type Task, type ToolConfig,
} from "@/lib/api";
import {
  announcement, appendTranscript, detectFinished, matchTasks, parseToolCall, planMove, startTaskError, taskLabel, voiceColumn,
  type RunOutcome, type VoiceLine, type VoiceToolCall,
} from "@/lib/voice-dispatcher";
import { readTaskScreen, scrollTaskScreen, type ScrollAmount, type ScrollDirection } from "@/lib/voice-viewport";
import { collectLinks, openLinkInNewTab, readTaskLinks, resolveLinkRef } from "@/lib/voice-links";

export type VoiceState = "idle" | "connecting" | "listening" | "working" | "speaking";

export interface VoiceAction {
  id: string;
  label: string;
  ok: boolean;
  /** Task the action touched; the panel links to it. */
  conversationId?: string;
  /** Validated links from the open task, shown as numbered chips to tap. */
  links?: Array<{ number: number; label: string; href: string }>;
}

/** Live audio is billed per second, so a forgotten session ends itself. */
const IDLE_LIMIT_MS = 3 * 60 * 1000;
const SESSION_LIMIT_MS = 20 * 60 * 1000;
const CLOSE_TIMEOUT_MS = 5000;
const ICE_TIMEOUT_MS = 10_000;
/** Captions arrive in bursts; hold "speaking" briefly between them. */
const SPEAKING_HOLD_MS = 1500;
/** How often the board is checked for tasks that stopped running. */
const BOARD_POLL_MS = 4000;
/** An announcement waits for this much quiet from the user. */
const QUIET_BEFORE_ANNOUNCE_MS = 1500;
/** "End voice": how long the goodbye may take to start, and the most it may run. */
const GOODBYE_WAIT_MS = 4000;
const GOODBYE_LIMIT_MS = 10_000;
const SCROLL_DIRECTIONS: ScrollDirection[] = ["up", "down", "top", "bottom"];
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
 * routes. Voice only hands out, reads, steers, stops, moves, opens and closes tasks,
 * scrolls and reads the task that is open, opens a link from its replies, and ends itself when asked.
 * While it is on it also watches the board and announces tasks that stop. */
export function useVoiceDispatcher({ boardId, model, toolConfig, onBoardChanged, onOpenTask, onCloseTask, onTaskActivity }: {
  boardId?: string;
  model?: string;
  toolConfig?: ToolConfig;
  /** Called after voice changes the board so it refreshes right away. */
  onBoardChanged: () => void;
  /** Shows a task without leaving the board (the desktop chat drawer). Where
   * there is none, open_task leaves a link in the panel instead. */
  onOpenTask?: (task: Task) => void;
  /** Closes the task shown by onOpenTask (or opened by hand) and returns it,
   * or null when no task is open. */
  onCloseTask?: () => Task | null;
  /** Fired with the conversation id whenever voice changes a specific task's
   * run or messages (create, start, steer, stop, move, or a watched finish).
   * Lets an open drawer for that task refresh its transcript in place. */
  onTaskActivity?: (conversationId: string) => void;
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
  const startedDraftsRef = useRef(new Set<string>());
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
  const onOpenTaskRef = useRef(onOpenTask);
  onOpenTaskRef.current = onOpenTask;
  const onCloseTaskRef = useRef(onCloseTask);
  onCloseTaskRef.current = onCloseTask;
  const onTaskActivityRef = useRef(onTaskActivity);
  onTaskActivityRef.current = onTaskActivity;
  // Task id -> column it was last seen in. A task seen in Working is announced when it stops.
  const watchedRef = useRef(new Map<string, string>());
  const announceRef = useRef<Array<{ title: string; outcome: RunOutcome; reply?: string | null }>>([]);
  const lastPollRef = useRef(0);
  const pollingRef = useRef(false);
  const lastUserSpeechRef = useRef(0);
  // Set when the user asked to end voice: the session closes once Loma has said goodbye.
  const endingRef = useRef<{ at: number; quietAt: number; spoke: boolean } | null>(null);

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
    startedDraftsRef.current.clear();
    watchedRef.current.clear();
    announceRef.current = [];
    pollingRef.current = false;
    endingRef.current = null;
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

  const logAction = useCallback((label: string, ok: boolean, conversationId?: string, links?: VoiceAction["links"]) => {
    setActions((prev) => [...prev, { id: `${Date.now()}-${prev.length}`, label, ok, conversationId, links }].slice(-20));
  }, []);

  const runTool = useCallback(async (call: VoiceToolCall): Promise<ToolResult> => {
    const text = (key: string) => (typeof call.args[key] === "string" ? (call.args[key] as string).trim() : "");
    const loadBoard = () => fetchTasksBoard("", boardIdRef.current);
    const loadTasks = async () => (await loadBoard()).tasks;
    const watched = watchedRef.current;
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
          const board = await loadBoard();
          const laneName = new Map(board.lanes.map((l) => [l.id, l.name]));
          const tasks = board.tasks
            .map((t) => ({
              id: t.conversation_id, title: taskLabel(t), column: voiceColumn(t.column),
              ...(laneName.has(t.column) ? { lane: laneName.get(t.column) } : {}),
            }))
            .filter((t) => (column === "all" ? t.column !== "done" : t.column === column));
          return { total: tasks.length, tasks: tasks.slice(0, LIST_LIMIT), lanes: board.lanes.map((l) => l.name) };
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
          // Watch it from now, so a run that ends before the next board check is still announced.
          if (start) watched.set(task.conversation_id, "working");
          onBoardChangedRef.current();
          onTaskActivityRef.current?.(task.conversation_id);
          return { ok: true, id: task.conversation_id, title: taskLabel(task), state: start ? "running" : "draft" };
        }
        case "start_task": {
          const found = await resolve();
          if ("result" in found) return found.result;
          const id = found.task.conversation_id;
          const startedDrafts = startedDraftsRef.current;
          if (startedDrafts.has(id)) return { error: "That draft has already been sent to start. Check its status." };
          startedDrafts.add(id);
          let accepted = false;
          try {
            const { conversation } = await fetchConversation(id);
            const reason = startTaskError({ ...found.task, ...conversation });
            if (reason) return { error: reason };
            const outcome = await sendTaskMessage(id, conversation.prompt, {
              model: conversation.model || undefined,
              tool_config: conversation.tool_config || undefined,
              files: conversation.draft_files,
            });
            accepted = outcome === "started" || outcome === "queued";
            if (!accepted) return { error: "The task is already busy. Check its status before trying again." };
            if (!found.task.star?.done && !found.task.star?.parked) watched.set(id, "working");
            logAction(`Started: ${taskLabel(found.task)}`, true, id);
            onBoardChangedRef.current();
            onTaskActivityRef.current?.(id);
            return { ok: true, title: taskLabel(found.task), state: outcome === "queued" ? "queued" : "running" };
          } finally {
            if (!accepted) startedDrafts.delete(id);
          }
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
          if (outcome === "started" || outcome === "queued") watched.set(found.task.conversation_id, "working");
          onBoardChangedRef.current();
          onTaskActivityRef.current?.(found.task.conversation_id);
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
          // The user stopped it themselves: nothing to announce.
          watched.set(found.task.conversation_id, "needs_input");
          onBoardChangedRef.current();
          onTaskActivityRef.current?.(found.task.conversation_id);
          return { ok: true, title: taskLabel(found.task), stopped: true };
        }
        case "move_task": {
          const ref = text("task");
          if (!ref) return { error: "Say which task." };
          const board = await loadBoard();
          const wanted = text("to").toLowerCase();
          // "Reopen X" means the finished one, which matchTasks otherwise ranks last.
          const reopening = wanted === "needs_input" || wanted === "needs input" || wanted === "reopen";
          const done = reopening ? matchTasks(board.tasks.filter((t) => t.column === "done"), ref) : [];
          const found = done.length ? done : matchTasks(board.tasks, ref);
          if (found.length === 0) return { error: `No task matches "${ref}".` };
          if (found.length > 1) return { ambiguous: true, candidates: found.slice(0, 5).map(taskLabel) };
          const task = found[0];
          const move = planMove(task, text("to"), board.lanes);
          if ("error" in move) return { error: move.error, title: taskLabel(task) };
          if (move.starUpdates) await updateTaskStar(task.conversation_id, move.starUpdates);
          else await updateTask(task.conversation_id, move.updates);
          logAction(`Moved to ${move.destination}: ${taskLabel(task)}`, true, task.conversation_id);
          watched.set(task.conversation_id, voiceColumn(move.column));
          onBoardChangedRef.current();
          onTaskActivityRef.current?.(task.conversation_id);
          return { ok: true, title: taskLabel(task), moved_to: move.destination, scope: task.star ? "your bookmark only" : "task" };
        }
        case "open_task": {
          const found = await resolve();
          if ("result" in found) return found.result;
          const open = onOpenTaskRef.current;
          if (open) open(found.task);
          logAction(`${open ? "Opened" : "Tap to open"}: ${taskLabel(found.task)}`, true, found.task.conversation_id);
          return { ok: true, title: taskLabel(found.task), shown: open ? "screen" : "link" };
        }
        case "close_task": {
          const closed = onCloseTaskRef.current?.() ?? null;
          if (!closed) return { closed: false, reason: "No task is open on the screen." };
          logAction(`Closed: ${taskLabel(closed)}`, true, closed.conversation_id);
          return { ok: true, closed: true, title: taskLabel(closed) };
        }
        case "scroll_task": {
          const direction = text("direction") as ScrollDirection;
          if (!SCROLL_DIRECTIONS.includes(direction)) return { error: "Say which way to scroll: up, down, top or bottom." };
          const amount: ScrollAmount = text("amount") === "half" ? "half" : "page";
          const screen = scrollTaskScreen(direction, amount);
          if (!screen) return { scrolled: false, reason: "No task is open on the screen." };
          const { moved, ...shown } = screen;
          if (!moved) {
            const edge = direction === "up" || direction === "top" ? "top" : "bottom";
            return { scrolled: false, reason: `Already at the ${edge} of the task.`, ...shown };
          }
          return { ok: true, scrolled: true, ...shown };
        }
        case "read_screen": {
          const screen = readTaskScreen();
          if (!screen) return { task_open: false, showing: "The task board. No task is open." };
          return { task_open: true, ...screen };
        }
        case "open_link": {
          // Only links the open task's agent showed; URLs and labels stay out of the result.
          const screen = readTaskLinks();
          if (!screen) return { opened: false, reason: "No task is open on the screen. Open the task first." };
          const links = collectLinks(screen.links);
          const numbered = new Map(links.map((l, i) => [l.href, i + 1]));
          const chip = (l: (typeof links)[number]) => ({ number: numbered.get(l.href) ?? 0, label: l.label, href: l.href });
          const choice = resolveLinkRef(links, text("link"));
          if ("none" in choice) return { opened: false, reason: choice.none, links_in_task: links.length };
          if ("ambiguous" in choice) {
            logAction("Which link? Tap one or say its number", true, undefined, choice.ambiguous.slice(0, 6).map(chip));
            return {
              opened: false, ambiguous: true, count: choice.ambiguous.length,
              choices: choice.ambiguous.slice(0, 6).map((l) => ({ number: numbered.get(l.href), kind: l.kind })),
            };
          }
          const { link } = choice;
          if (openLinkInNewTab(link.href)) {
            logAction(`Opened ${link.kind}: ${link.label}`, true, undefined, [chip(link)]);
            return { ok: true, opened: true, kind: link.kind };
          }
          logAction(`Tap to open ${link.kind}: ${link.label}`, true, undefined, [chip(link)]);
          return { opened: false, blocked: true, kind: link.kind, reason: "The browser blocked the new tab. A link is on screen to tap." };
        }
        case "end_voice": {
          endingRef.current ??= { at: Date.now(), quietAt: 0, spoke: false };
          logAction("Ending voice mode", true);
          return { ok: true, ending: true };
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

  /** Look at the board; queue an announcement for every watched task that stopped. */
  const pollBoard = useCallback(async () => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    const generation = generationRef.current;
    try {
      const { tasks } = await fetchTasksBoard("", boardIdRef.current);
      if (generation !== generationRef.current) return;
      const finished = detectFinished(watchedRef.current, tasks);
      if (!finished.length) return;
      const items = await Promise.all(finished.map(async ({ task, outcome }) => {
        // The reply is a nicety: announce the task even if it can't be read.
        const reply = await fetchConversation(task.conversation_id)
          .then((r) => r.conversation.final_response || null).catch(() => null);
        return { id: task.conversation_id, title: taskLabel(task), outcome, reply };
      }));
      if (generation !== generationRef.current) return;
      for (const item of items) {
        logAction(`${item.outcome === "failed" ? "Failed" : item.outcome === "needs_input" ? "Needs you" : "Finished"}: ${item.title}`,
          item.outcome !== "failed", item.id);
        onTaskActivityRef.current?.(item.id);
      }
      announceRef.current.push(...items);
      onBoardChangedRef.current();
    } catch {
      // A missed check is fine: the next one compares against the same snapshot.
    } finally {
      if (generation === generationRef.current) pollingRef.current = false;
    }
  }, [logAction]);

  /** Speak queued announcements once nobody is talking and no request is in flight. */
  const flushAnnouncements = useCallback((now: number) => {
    if (!announceRef.current.length || busyRef.current > 0 || pendingRef.current.length) return;
    if (now < speakingUntilRef.current || now - lastUserSpeechRef.current < QUIET_BEFORE_ANNOUNCE_MS) return;
    const content = announcement(announceRef.current);
    announceRef.current = [];
    // General session context (no delegation): the voice model says it in its own words.
    send({ type: "session.commentary.append", delegation_id: null, content });
    speakingUntilRef.current = now + SPEAKING_HOLD_MS;
    lastActivityRef.current = now;
  }, [send]);

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
        if (speaker === "assistant") {
          speakingUntilRef.current = Date.now() + SPEAKING_HOLD_MS;
          // Speech after the backend finished is the goodbye, not a "one moment" filler.
          if (endingRef.current && busyRef.current === 0) endingRef.current.spoke = true;
        } else lastUserSpeechRef.current = Date.now();
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
      lastPollRef.current = 0;
      timerRef.current = setInterval(() => {
        const now = Date.now();
        if (now - startedAtRef.current > SESSION_LIMIT_MS) return stop("Voice ended after 20 minutes.");
        // Stay on while a task it is watching still runs, so the finish can be announced.
        let watching = announceRef.current.length > 0;
        for (const column of watchedRef.current.values()) watching ||= column === "working";
        if (now - lastActivityRef.current > IDLE_LIMIT_MS && busyRef.current === 0 && !watching) return stop("Voice ended after 3 quiet minutes.");
        if (!readyRef.current) {
          if (now - startedAtRef.current > 30_000) stop("Voice connection timed out. Please try again.");
          return;
        }
        if (now - lastPollRef.current >= BOARD_POLL_MS) {
          lastPollRef.current = now;
          void pollBoard();
        }
        // Asked to end: let Loma finish its goodbye, then close as the End button does.
        const ending = endingRef.current;
        if (ending) {
          const idle = busyRef.current === 0 && pendingRef.current.length === 0;
          if (idle && !ending.quietAt) ending.quietAt = now;
          const saidGoodbye = idle && ending.spoke && now >= speakingUntilRef.current;
          const noGoodbye = idle && !ending.spoke && now - ending.quietAt > GOODBYE_WAIT_MS;
          if (saidGoodbye || noGoodbye || now - ending.at > GOODBYE_LIMIT_MS) return stop();
          setState(now < speakingUntilRef.current ? "speaking" : "working");
          return;
        }
        flushAnnouncements(now);
        setState(now < speakingUntilRef.current ? "speaking" : busyRef.current > 0 ? "working" : "listening");
      }, 300);
    } catch (e) {
      if (peerRef.current !== peer) return;
      const denied = e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "NotFoundError");
      cleanup(denied ? "Microphone access is needed for voice mode." : e instanceof Error ? e.message : "Could not start voice mode");
    }
  }, [cleanup, flushAnnouncements, handleEvent, pollBoard, stop]);

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
