"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { terminalKind, withTerminalStatus, STOPPED_BY_USER_MESSAGE } from "../lib/terminal-status";
import { useSession } from "next-auth/react";
import { useStandalone } from "@/hooks/useStandalone";
import { useKeyboardVisible } from "@/hooks/useKeyboardVisible";
import { ComposerSettings } from "./composer/ComposerSettings";
import { useIsMobile } from "@/hooks/useIsMobile";
import { useAgentModels } from "@/hooks/useAgentModels";
import { useAgentIdentities } from "@/hooks/useAgentIdentities";
import { AgentPicker } from "@/components/composer/AgentPicker";
import { AgentScope } from "@/components/composer/AgentScope";
import { AGENT_TOOL_CONFIG } from "@/hooks/agent-scope";
import { useToolsPicker } from "@/hooks/useToolsPicker";
import { filesToChatFiles, filesFromClipboard } from "@/lib/chatFiles";
import { ModelPicker } from "./composer/ModelPicker";
import { ToolsPicker } from "./composer/ToolsPicker";
import { PendingFilesStrip } from "./composer/PendingFilesStrip";
import { useFileDrop } from "./composer/useFileDrop";
import { DictationButton, appendDictation } from "./composer/DictationButton";
import { VoiceModeButton, VoicePanel } from "./tasks/VoiceDispatcher";
import { useVoiceAvailable, useVoiceDispatcher } from "@/hooks/useVoiceDispatcher";
import type { ChatVoiceState, VoiceChatMessage } from "@/lib/voice-chat";
import { streamChat, fetchConversation, injectMessage, interruptAgent, basePath, ConversationBusyError, ConversationQueuedError, ChatRequestError } from "../lib/api";
import type { ChatEvent, ChatFile, ChatMessage, ClarifyQuestion, Turn, PersistedArtifact } from "../lib/api";
import MarkdownContent from "./MarkdownContent";
import ArtifactCard from "./ArtifactCard";
import { CopyButton } from "./CopyButton";
import { isDeadRun } from "@/lib/server-time";
import { withoutArtifactSource } from "@/lib/artifact-fences";
import { isProcessItem, processCounts, processIndices, processLabel, stepsWord } from "@/lib/process-group";
import { SlashCommandHint, SlashCommandMenu, slashMatches } from "./composer/SlashCommandMenu";
import { matchCommand, usePromptTemplates } from "@/hooks/usePromptTemplates";
import { approvalMessage, revisionMessage, type PlanComment, type PlanStatus } from "./PlanReview";
import type { Artifact } from "./ArtifactViewer";
import PetCompanion, { PetRunway } from "./PetCompanion";
import CrosscutIcon from "./CrosscutIcon";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { AgentAvatar } from "@/components/AgentAvatar";
import {
  DEFAULT_AGENT_NAME, agentForTime, type AttributedMessage,
} from "@/lib/agent-attribution";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  RiCloseLine,
  RiArrowDownSLine,
  RiSendPlaneLine,
  RiLoader4Line,
  RiPauseLine,
  RiAttachmentLine,
  RiCheckLine,
  RiUploadLine,
  RiDownloadLine,
  RiStopLine,
  RiTimeLine,
  RiEditLine,
  RiDeleteBinLine,
  RiListCheck3,
  RiLightbulbLine,
} from "@remixicon/react";

const RECOVERY_MESSAGE = "Connection lost — checking on your request...";
/** Conversation status while its message waits for a deploy to finish. */
const DEPLOY_QUEUED_STATUS = "queued";

/** Tag the newest user message as waiting for a deploy (server-side queue). */
function markLatestUserDeployQueued(items: ChatItem[], on: boolean): ChatItem[] {
  const idx = items.map((item) => item.role).lastIndexOf("user");
  if (idx < 0 || !!items[idx].deployQueued === on) return items;
  return items.map((item, i) => (i === idx ? { ...item, deployQueued: on } : item));
}

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);

function createConversationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function isImageFile(name: string): boolean {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return IMAGE_EXTENSIONS.has(ext);
}

interface Step {
  type: "tool_call" | "tool_result";
  name?: string;
  tool_use_id: string;
  is_error?: boolean;
  status: "running" | "done" | "error";
  input?: string;
}

/** A file attachment delivered by the agent */
export interface FileAttachment {
  file_id: string;
  name: string;
  url: string;
  mime_type: string;
  size: number;
}

/** What the side panel needs to review plans in this chat. */
export interface PlanState {
  /** Plan artifacts in chat order, versioned v1, v2, ... */
  plans: Artifact[];
  statuses: Record<string, PlanStatus>;
  canAct: boolean;
}

export interface PlanControl {
  revise: (plan: Artifact, comments: PlanComment[]) => void;
  approve: (plan: Artifact, notes: PlanComment[]) => void;
}

export interface ChatItem {
  role: "user" | "assistant" | "steps" | "clarify" | "status" | "thinking";
  content: string;
  steps?: Step[];
  fileNames?: string[];
  files?: ChatFile[];
  questions?: ClarifyQuestion[];
  submitted?: boolean;
  selectedLabels?: string[];
  /** Artifact IDs referenced by this message (for inline artifact cards) */
  artifactIds?: string[];
  /** Assistant items: the agent that wrote it (Loma when unset). */
  agentName?: string;
  agentId?: string | null;
  /** User items sent from this tab: the agent that will answer. */
  replyAgentName?: string;
  replyAgentId?: string | null;
  /** File attachments delivered by the agent */
  fileAttachments?: FileAttachment[];
  /** Client-observed response duration for this assistant message */
  responseTimeSeconds?: number;
  elapsedSeconds?: number;
  /** True when this user message is queued to be sent after the current stream finishes */
  queued?: boolean;
  /** True when the server saved this message during a deploy and will run it once Loma is back */
  deployQueued?: boolean;
  /** Who sent this user message (shared-board tasks: the run used their accounts). */
  sender?: string;
  /** Thinking items: when the thought started streaming, and how long it took once done. */
  thinkingStartedAt?: number;
  thinkingSeconds?: number;
}

const planModeKey = (conversationId: string | null | undefined) => `loma-plan-mode:${conversationId || "new"}`;

function readPlanMode(key: string): boolean {
  try { return window.localStorage.getItem(key) === "1"; } catch { return false; }
}

function writePlanMode(key: string, on: boolean) {
  try {
    if (on) window.localStorage.setItem(key, "1");
    else window.localStorage.removeItem(key);
  } catch { /* private mode: plan mode just isn't remembered */ }
}

/** Pretty-print a tool name for display */
function formatToolName(name: string): string {
  if (name.startsWith("mcp__")) {
    const parts = name.replace("mcp__", "").split("__");
    return parts.map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" ");
  }
  return name;
}



const noop = () => {};

/** What voice mode sees of the chat: is the agent running, its current step and latest reply. */
function chatVoiceState(items: ChatItem[], running: boolean, streamStartedAt: number | null): ChatVoiceState {
  let latestReply: string | null = null;
  for (let i = items.length - 1; i >= 0 && latestReply === null; i--) {
    if (items[i].role === "assistant" && items[i].content.trim()) latestReply = items[i].content;
  }
  let currentStep: string | null = null;
  let waitingForUser = false;
  for (let i = items.length - 1; i >= 0 && items[i].role !== "user"; i--) {
    const item = items[i];
    if (item.role === "clarify" && !item.submitted) waitingForUser = true;
    if (currentStep) continue;
    if (item.role === "status" && item.content) currentStep = item.content;
    const step = item.role === "steps" ? item.steps?.[item.steps.length - 1] : undefined;
    if (step?.name) currentStep = `${step.status === "running" ? "running" : "ran"} ${formatToolName(step.name)}`;
  }
  // streamStartedAt is a performance.now() reading.
  const startedAt = running && streamStartedAt !== null ? Date.now() - (performance.now() - streamStartedAt) : null;
  return { running, startedAt, currentStep: running ? currentStep : null, latestReply, waitingForUser };
}

/** The chat's user and Loma messages, oldest first, for voice to read. */
function chatVoiceMessages(items: ChatItem[]): VoiceChatMessage[] {
  return items
    .filter((item) => (item.role === "user" || item.role === "assistant") && item.content.trim())
    .map((item) => ({ role: item.role === "user" ? "user" as const : "loma" as const, text: item.content }));
}

/** Extract a concise summary from a tool's raw JSON input string (for historical turns) */
function summarizeToolInput(toolName: string, rawInput: string): string {
  if (!rawInput) return "";
  try {
    const input = JSON.parse(rawInput);
    if (typeof input !== "object" || input === null) return "";
    const name = toolName.toLowerCase();

    if (name === "read" || name === "readfile") return input.file_path || "";
    if (name === "bash") return input.description || (input.command || "").slice(0, 200);
    if (name === "grep") {
      const parts = ['"' + (input.pattern || '') + '"'];
      if (input.path) parts.push("in " + input.path);
      if (input.glob) parts.push("(" + input.glob + ")");
      return parts.join(" ");
    }
    if (name === "glob") return (input.pattern || "") + (input.path ? " in " + input.path : "");
    if (name === "edit" || name === "write") return input.file_path || "";
    if (name === "task") return input.description || (input.prompt || "").slice(0, 200);
    if (name === "webfetch") return input.url || "";
    if (name === "websearch") return input.query || "";
    if (name === "skill") return (input.skill || "") + (input.args ? " " + input.args : "");
    if (name === "todowrite") {
      const todos = input.todos;
      if (Array.isArray(todos) && todos.length) {
        const ip = todos.find((t: Record<string, string>) => t.status === "in_progress");
        if (ip) return ip.activeForm || ip.content || "";
        return todos.length + " items";
      }
      return "";
    }
    if (name === "toolsearch") return input.query || "";
    if (name.startsWith("mcp__")) {
      for (const key of ["query", "owner", "pattern", "command", "url", "message", "body", "title", "name", "path"]) {
        if (input[key] && typeof input[key] === "string") return key + ": " + input[key].slice(0, 200);
      }
    }
    // Fallback: first string value
    for (const [key, val] of Object.entries(input)) {
      if (typeof val === "string" && val) return key + ": " + (val as string).slice(0, 200);
    }
  } catch {
    // If JSON parse fails, return truncated raw input
    return rawInput.slice(0, 100);
  }
  return "";
}

/** Convert items array to conversation history for the API */
function buildHistory(items: ChatItem[]): ChatMessage[] {
  const history: ChatMessage[] = [];
  for (const item of items) {
    if (item.role === "user" || item.role === "assistant") {
      if (item.content) {
        history.push({ role: item.role, content: item.content });
      }
    }
    if (item.role === "status" || item.role === "steps") {
      continue;
    }
    if (item.role === "clarify" && item.content) {
      history.push({ role: "assistant", content: item.content });
    }
  }
  return history;
}

const LIVE_PROCESS_ENTRIES = 4;

function formatResponseTime(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}

function WorkingIndicator({ elapsedSeconds, status, detail }: {
  elapsedSeconds: number;
  /** Latest backend status (e.g. "Starting session"), shown beside the timer. */
  status?: string;
  /** Which model/account is running this, revealed on hover. */
  detail?: string;
}) {
  const display = elapsedSeconds < 10
    ? `${elapsedSeconds.toFixed(1)}s`
    : elapsedSeconds < 60
      ? `${elapsedSeconds.toFixed(0)}s`
      : `${Math.floor(elapsedSeconds / 60)}m ${Math.floor(elapsedSeconds % 60).toString().padStart(2, "0")}s`;

  return (
    <div className="working-indicator flex items-center gap-2 py-1.5 text-[14px] text-muted-foreground" title={detail}>
      <span className="grid grid-cols-3 gap-[3px]">
        {Array.from({ length: 9 }, (_, i) => (
          <span
            key={i}
            className="working-grid-dot block w-[3px] h-[3px] rounded-full bg-current"
            style={{ animationDelay: `${(i % 3) * 0.15 + Math.floor(i / 3) * 0.1}s` }}
          />
        ))}
      </span>
      <span className="tabular-nums font-mono text-[13px]">
        {display}
      </span>
      {status && <span className="truncate max-w-[420px]">{status}</span>}
    </div>
  );
}

type MessageBlock = { kind: "user"; index: number } | { kind: "agent"; indices: number[] };

/** One block per user message, and one per agent reply (its steps, notes and answer). */
function groupIntoBlocks(items: ChatItem[]): MessageBlock[] {
  const blocks: MessageBlock[] = [];
  items.forEach((item, index) => {
    const last = blocks[blocks.length - 1];
    if (item.role === "user") blocks.push({ kind: "user", index });
    else if (last?.kind === "agent") last.indices.push(index);
    else blocks.push({ kind: "agent", indices: [index] });
  });
  return blocks;
}

/** One-line summary of the account/model serving this run, for a hover hint. */
function describeAccount(info: {
  runtime?: string; provider?: string; model?: string; account_email?: string;
  pool_available?: number; pool_size?: number;
} | null): string | undefined {
  if (!info) return undefined;
  const pool = typeof info.pool_available === "number" && typeof info.pool_size === "number"
    ? ` · ${info.pool_available}/${info.pool_size} available`
    : "";
  if (info.runtime === "opencode") return `${info.provider}/${info.model} via OpenCode${pool}`;
  const product = info.runtime === "codex" ? "ChatGPT" : "Claude";
  return `${info.model ? `${info.model} via ` : ""}${info.account_email || "unknown"}'s ${product} subscription${pool}`;
}

function stampLatestAssistantDuration(items: ChatItem[], responseTimeSeconds: number): ChatItem[] {
  const updated = [...items];
  for (let i = updated.length - 1; i >= 0; i--) {
    if (updated[i].role === "assistant") {
      updated[i] = { ...updated[i], responseTimeSeconds };
      return updated;
    }
  }
  return updated;
}

function removeTransientStatusItems(items: ChatItem[]): ChatItem[] {
  const filtered = items.filter((item) => item.role !== "status");
  return filtered.length === items.length ? items : filtered;
}

/** Extract a :::clarify block from text content */
function extractClarifyBlock(text: string): {
  questions: ClarifyQuestion[];
  before: string;
  after: string;
} | null {
  const match = text.match(/:::clarify\s*\n([\s\S]*?)\n:::/);
  if (!match) return null;

  try {
    const parsed = JSON.parse(match[1]);
    if (!parsed.questions || !Array.isArray(parsed.questions)) return null;

    const before = text.slice(0, match.index).trim();
    const after = text.slice(match.index! + match[0].length).trim();

    return { questions: parsed.questions, before, after };
  } catch {
    return null;
  }
}

/**
 * Rebuild ChatItem[] from conversation data + turns, including tool steps.
 * Each turn may contain tool_calls, tool_results, and text_blocks.
 */
export function rebuildItemsFromConversation(
  messages: Array<{
    role: string; content: string; timestamp?: string; sender?: string;
    agent_id?: string | null; agent_name?: string;
  }> | undefined,
  prompt: string,
  finalResponse: string,
  turns: Turn[],
  persistedArtifacts?: PersistedArtifact[],
): { items: ChatItem[]; artifacts: Artifact[] } {
  const { items, artifacts } = rebuildTranscript(messages, prompt, finalResponse, turns, persistedArtifacts);
  return { items: restoreClarifyItems(items), artifacts };
}

/**
 * A `:::clarify` block is parsed from live text only, so after a reload it
 * showed as raw JSON. Rebuild it as a question card; it counts as answered
 * once the user has sent anything after it.
 */
function restoreClarifyItems(items: ChatItem[]): ChatItem[] {
  const out: ChatItem[] = [];
  items.forEach((item, i) => {
    const clarify = item.role === "assistant" ? extractClarifyBlock(item.content) : null;
    if (!clarify) {
      out.push(item);
      return;
    }
    const answer = items.slice(i + 1).find((later) => later.role === "user");
    out.push({
      ...item,
      role: "clarify",
      content: clarify.before,
      questions: clarify.questions,
      submitted: !!answer,
      selectedLabels: answer
        ? clarify.questions.flatMap((q) => q.options.map((o) => o.label)).filter((label) => answer.content.includes(label))
        : undefined,
    });
    if (clarify.after) out.push({ ...item, content: clarify.after, artifactIds: undefined });
  });
  return out;
}

function rebuildTranscript(
  messages: Parameters<typeof rebuildItemsFromConversation>[0],
  prompt: string,
  finalResponse: string,
  turns: Turn[],
  persistedArtifacts?: PersistedArtifact[],
): { items: ChatItem[]; artifacts: Artifact[] } {
  // The first message actually sent. A conversation's stored `prompt` can be
  // empty or stale (e.g. a board draft edited before sending), which used to
  // leave an empty first bubble that rendered as a typing indicator forever.
  const firstUserMessage = messages?.find((m) => m.role === "user");
  const initialPrompt = firstUserMessage?.content || prompt || "";

  // If no turns data, fall back to simple message reconstruction
  if (!turns?.length) {
    if (messages?.length) {
      return {
        items: messages.map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
          ...(m.role === "user" && m.sender ? { sender: m.sender } : {}),
          ...(m.role === "assistant"
            ? { agentId: m.agent_id ?? null, agentName: m.agent_name || DEFAULT_AGENT_NAME }
            : {}),
        })),
        artifacts: [],
      };
    }
    const items: ChatItem[] = initialPrompt ? [{ role: "user", content: initialPrompt }] : [];
    if (finalResponse) {
      items.push({ role: "assistant", content: finalResponse });
    }
    return { items, artifacts: [] };
  }

  // Collect follow-up user messages (skip the first one — it's the initial prompt)
  const followUpUserMessages: Array<{ content: string; timestamp: string; sender?: string }> = [];
  if (messages && messages.length > 1) {
    const userMessages = messages.filter((m) => m.role === "user");
    for (let i = 1; i < userMessages.length; i++) {
      if (userMessages[i].timestamp) {
        followUpUserMessages.push({
          content: userMessages[i].content,
          timestamp: userMessages[i].timestamp!,
          sender: userMessages[i].sender,
        });
      }
    }
  }
  const firstSender = messages?.find((m) => m.role === "user")?.sender;

  // Track which follow-up messages have been inserted
  const insertedFollowUps = new Set<number>();

  // Start with the initial user message
  const items: ChatItem[] = initialPrompt
    ? [{ role: "user", content: initialPrompt, ...(firstSender ? { sender: firstSender } : {}) }]
    : [];

  // Where each turn's items begin, so artifacts can find the reply they belong to.
  const turnStartIndex: number[] = [];

  // Each turn represents one assistant response cycle (possibly with tool calls)
  for (const turn of turns) {
    // Before processing this turn, insert any follow-up user messages
    // whose timestamp is before this turn's timestamp
    const turnTime = turn.timestamp;
    for (let i = 0; i < followUpUserMessages.length; i++) {
      if (insertedFollowUps.has(i)) continue;
      if (followUpUserMessages[i].timestamp < turnTime) {
        const exists = items.some(
          (item) => item.role === "user" && item.content === followUpUserMessages[i].content
        );
        if (!exists) {
          items.push({ role: "user", content: followUpUserMessages[i].content, sender: followUpUserMessages[i].sender });
        }
        insertedFollowUps.add(i);
      }
    }

    turnStartIndex.push(items.length);
    for (const block of turn.thinking_blocks || []) {
      if (block.text?.trim()) items.push({ role: "thinking", content: block.text });
    }
    const toolCalls = turn.tool_calls || [];
    const toolResults = turn.tool_results || [];
    if (toolCalls.length > 0) {
      const resultMap = new Map(toolResults.map((r) => [r.tool_use_id, r]));
      const steps = toolCalls.map((tc) => {
        const result = resultMap.get(tc.tool_use_id);
        return {
          type: "tool_call" as const,
          name: tc.tool_name,
          tool_use_id: tc.tool_use_id,
          is_error: result?.is_error,
          status: (result ? (result.is_error ? "error" : "done") : "done") as "done" | "error",
          input: summarizeToolInput(tc.tool_name, tc.input),
        };
      });
      // Runs usually make one tool call per turn: fold back-to-back calls into
      // one group, as the live stream does.
      const last = items[items.length - 1];
      if (last?.role === "steps") {
        items[items.length - 1] = { ...last, steps: [...(last.steps || []), ...steps] };
      } else {
        items.push({ role: "steps", content: "", steps });
      }
    }

    const textBlocks = turn.text_blocks || [];
    const text = textBlocks.map((b) => b.text).join("\n\n").trim();
    if (text) {
      // Unknown while the run is still going: the panel then uses the agent
      // the message was sent to.
      const author = agentForTime(messages as AttributedMessage[] | undefined, turn.timestamp);
      items.push({ role: "assistant", content: text, ...(author || {}) });
    }
  }

  // Append any remaining follow-up user messages that weren't inserted
  // (e.g., messages sent after all turns completed)
  for (let i = 0; i < followUpUserMessages.length; i++) {
    if (insertedFollowUps.has(i)) continue;
    const exists = items.some(
      (item) => item.role === "user" && item.content === followUpUserMessages[i].content
    );
    if (!exists) {
      items.push({ role: "user", content: followUpUserMessages[i].content, sender: followUpUserMessages[i].sender });
    }
  }

  // Also handle follow-up user messages without timestamps (fallback for old data)
  if (messages && messages.length > 1) {
    const userMessages = messages.filter((m) => m.role === "user");
    for (let i = 1; i < userMessages.length; i++) {
      if (!userMessages[i].timestamp) {
        const exists = items.some(
          (item) => item.role === "user" && item.content === userMessages[i].content
        );
        if (!exists) {
          items.push({ role: "user", content: userMessages[i].content });
        }
      }
    }
  }

  // Restore persisted artifacts and attach artifact IDs to assistant messages
  const restoredArtifacts: Artifact[] = [];
  if (persistedArtifacts?.length) {
    for (const pa of persistedArtifacts) {
      restoredArtifacts.push({
        id: pa.artifact_id,
        title: pa.title,
        content: pa.content || "",
        language: pa.language,
        version: pa.version,
        timestamp: new Date(pa.timestamp).getTime(),
        file_url: pa.file_url,
        file_size: pa.file_size,
        file_type: pa.file_type,
      });
    }

    // Attach each artifact to the first reply written at or after the turn that
    // produced it (most turns are tool-only, so turn N is not the Nth reply),
    // else to the last reply.
    const turnTimestamps = turns.map((t) => t.timestamp);
    for (const pa of persistedArtifacts) {
      let bestTurnIdx = -1;
      for (let i = turnTimestamps.length - 1; i >= 0; i--) {
        if (turnTimestamps[i] <= pa.timestamp) {
          bestTurnIdx = i;
          break;
        }
      }
      const from = bestTurnIdx >= 0 ? turnStartIndex[bestTurnIdx] : items.length;
      let target = items.findIndex((item, i) => i >= from && item.role === "assistant");
      if (target < 0) target = items.map((item) => item.role).lastIndexOf("assistant");
      if (target < 0) continue;
      const existingIds = items[target].artifactIds || [];
      if (!existingIds.includes(pa.artifact_id)) {
        items[target] = { ...items[target], artifactIds: [...existingIds, pa.artifact_id] };
      }
    }
  }

  return { items, artifacts: restoredArtifacts };
}

/** Bouncing dots typing indicator */
function TypingIndicator() {
  return (
    <span className="flex items-center gap-1 text-gray-400 py-0.5">
      <span className="typing-dot" />
      <span className="typing-dot" />
      <span className="typing-dot" />
    </span>
  );
}

/** Image thumbnail for pending files */
/** Lightbox overlay for expanded image view */
function ImageLightbox({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-3 animate-fade-in cursor-pointer"
      onClick={onClose}
    >
      <div className="relative max-w-[90vw] max-h-[90dvh]" onClick={(e) => e.stopPropagation()}>
        <img
          src={src}
          alt="Expanded view"
          className="max-w-full max-h-[90dvh] object-contain rounded-lg shadow-2xl"
        />
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label="Close image"
          // Phones: inside the corner (the outside-corner offset is clipped at
          // the viewport edge); desktop keeps the original outside-corner button.
          className="absolute max-md:top-2 max-md:right-2 md:-top-3 md:-right-3 bg-gray-800 hover:bg-gray-700 text-white rounded-full shadow-lg"
        >
          <RiCloseLine size={16} />
        </Button>
      </div>
    </div>
  );
}

/** Format file size for display */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/** Get icon and color based on MIME type */
function getFileIcon(mimeType: string, name: string): { icon: string; color: string; bg: string } {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (mimeType.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext))
    return { icon: "🖼️", color: "text-purple-600", bg: "bg-purple-50" };
  if (mimeType === "application/pdf" || ext === "pdf")
    return { icon: "📄", color: "text-red-600", bg: "bg-red-50" };
  if (["xlsx", "xls", "csv"].includes(ext))
    return { icon: "📊", color: "text-green-600", bg: "bg-green-50" };
  if (["docx", "doc"].includes(ext))
    return { icon: "📝", color: "text-blue-600", bg: "bg-blue-50" };
  if (["pptx", "ppt"].includes(ext))
    return { icon: "📑", color: "text-orange-600", bg: "bg-orange-50" };
  if (["zip", "tar", "gz", "tgz"].includes(ext))
    return { icon: "📦", color: "text-yellow-700", bg: "bg-yellow-50" };
  return { icon: "📎", color: "text-gray-600", bg: "bg-gray-50" };
}

/** Inline file attachment card with download link */
function FileAttachmentCard({ file }: { file: FileAttachment }) {
  const { icon, color, bg } = getFileIcon(file.mime_type, file.name);
  const isImage = file.mime_type.startsWith("image/");
  const downloadUrl = `${basePath}${file.url}`;

  return (
    <a
      href={downloadUrl}
      download={file.name}
      target="_blank"
      rel="noopener noreferrer"
      className={`flex items-center gap-2 px-3 py-2.5 rounded-xl border border-gray-200 hover:border-gray-300 hover:shadow-sm transition-all group ${bg}`}
    >
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center text-lg flex-shrink-0 ${bg}`}>
        {isImage && file.size < 5 * 1024 * 1024 ? (
          <img
            src={downloadUrl}
            alt={file.name}
            className="w-10 h-10 rounded-lg object-cover"
          />
        ) : (
          <span>{icon}</span>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-medium text-gray-800 truncate group-hover:text-gray-900">
          {file.name}
        </p>
        <p className="text-xs text-muted-foreground">
          {formatFileSize(file.size)} · {file.mime_type.split("/")[1]?.toUpperCase() || "FILE"}
        </p>
      </div>
      <RiDownloadLine size={16} className={cn(color, "opacity-60 group-hover:opacity-100 flex-shrink-0 transition-opacity")} />
    </a>
  );
}

function ReadOnlyComposerNotice() {
  return (
    <p className="mx-auto max-w-3xl rounded-xl border border-dashed border-border px-3 py-2.5 text-center text-xs text-muted-foreground">
      View only. Owners and editors of this board can message this task. Each run uses the accounts of whoever sends the message.
    </p>
  );
}

export default function ChatPanel({
  initialItems,
  initialArtifacts,
  conversationId: initialConversationId,
  initialPrompt,
  initialFiles,
  initialModel,
  initialAgentId,
  initialToolConfig,
  autoSend,
  systemContext,
  initialStatus,
  draftStorageKey,
  activeArtifactId,
  onArtifactOpen,
  onArtifactClose,
  artifacts: externalArtifacts,
  onConversationCreated,
  onStreamComplete,
  readOnly = false,
  autoFocusComposer = true,
  onPlanStateChange,
  planControlRef,
}: {
  initialItems?: ChatItem[];
  /** Artifacts restored from history (persisted in MongoDB) */
  initialArtifacts?: Artifact[];
  conversationId?: string;
  initialPrompt?: string;
  /** Attachments staged with a board-task draft — seeded as pending files */
  initialFiles?: ChatFile[];
  /** Model to preselect (e.g. a board task's chosen model) — wins over the saved preference */
  initialModel?: string;
  /** Agent pinned on the conversation being resumed — wins over the saved preference */
  initialAgentId?: string | null;
  /** Tool/skill restrictions to preselect (e.g. from a board task's stored config) */
  initialToolConfig?: import("@/lib/api").ToolConfig | null;
  autoSend?: boolean;
  systemContext?: string;
  initialStatus?: string;
  /** When set, unsent composer text is persisted to localStorage under this
   * key and restored on mount — an accidental close never loses a draft. */
  draftStorageKey?: string;
  /** Currently active artifact ID (for highlighting the active card) */
  activeArtifactId?: string | null;
  /** Callback when user clicks an artifact card */
  onArtifactOpen?: (artifact: Artifact) => void;
  /** Callback when artifact viewer is closed */
  onArtifactClose?: () => void;
  /** All artifacts (managed by parent) */
  artifacts?: Artifact[];
  /** Called when a new conversation is created (ID available) */
  onConversationCreated?: (conversationId: string) => void;
  /** Called when the agent stream finishes (for post-stream title refresh) */
  onStreamComplete?: (conversationId: string) => void;
  /** Show the transcript without a composer (e.g. a teammate's task on a shared board). */
  readOnly?: boolean;
  /** Focus the composer on mount. Off where keyboard shortcuts drive the page. */
  autoFocusComposer?: boolean;
  /** Reports plan versions/statuses so the side panel can review them. */
  onPlanStateChange?: (state: PlanState) => void;
  /** Lets the side panel revise or approve a plan through this chat. */
  planControlRef?: React.MutableRefObject<PlanControl | null>;
} = {}) {
  const { data: session } = useSession();
  const standalone = useStandalone();
  const isMobile = useIsMobile();
  const keyboardOpen = useKeyboardVisible();
  const [items, setItems] = useState<ChatItem[]>(initialItems || []);
  // Shared-board tasks can have two people messaging (creator and assignee):
  // label each message with its sender only when that's the case.
  const multipleSenders = useMemo(
    () => new Set(items.filter((item) => item.role === "user" && item.sender).map((item) => item.sender)).size > 1,
    [items],
  );
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversationId);
  const [input, setInput] = useState(initialPrompt || "");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null);
  const [streamElapsedSeconds, setStreamElapsedSeconds] = useState(0);
  const [isRecovering, setIsRecovering] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<ChatFile[]>(initialFiles || []);
  const [expandedImage, setExpandedImage] = useState<string | null>(null);
  const [accountInfo, setAccountInfo] = useState<{
    account_type?: "round_robin";
    account_email?: string;
    pool_available?: number;
    pool_size?: number;
    pool_warming?: number;
    active_sessions?: number;
    warm_session_used?: boolean;
    runtime?: string;
    provider?: string;
    model?: string;
  } | null>(null);
  const {
    models: agentModels,
    selectedModel,
    selectModel,
    loadState: modelLoadState,
  } = useAgentModels(initialModel);
  const {
    agents: agentIdentities,
    selectedAgent,
    selectedAgentId,
    selectAgent,
    loadState: agentLoadState,
  } = useAgentIdentities(initialAgentId);
  const agentsById = useMemo(
    () => Object.fromEntries(agentIdentities.map((a) => [a.agent_id, a])),
    [agentIdentities],
  );
  // Name replies only once an agent is involved, so plain Loma chats look as before.
  const threadHasAgents = useMemo(
    () => !!selectedAgentId || items.some((it) => !!it.agentId || !!it.replyAgentId),
    [items, selectedAgentId],
  );
  /** Who wrote assistant item i: its saved agent, else the agent it was sent to. */
  const replyAuthor = (index: number): { agentId: string | null; agentName: string } => {
    const item = items[index];
    if (item?.agentName) return { agentId: item.agentId ?? null, agentName: item.agentName };
    for (let j = index - 1; j >= 0; j--) {
      const prev = items[j];
      if (prev.role === "user" && prev.replyAgentName) {
        return { agentId: prev.replyAgentId ?? null, agentName: prev.replyAgentName };
      }
      if (prev.role === "assistant" && prev.agentName) {
        return { agentId: prev.agentId ?? null, agentName: prev.agentName };
      }
    }
    return selectedAgent
      ? { agentId: selectedAgent.agent_id, agentName: selectedAgent.name }
      : { agentId: null, agentName: DEFAULT_AGENT_NAME };
  };
  const {
    tools: availableTools,
    skills: availableSkills,
    selection: toolsSelection,
    loadState: toolsLoadState,
    loadCatalog: loadToolsCatalog,
    setEnabled,
    setAll,
    toolConfig,
  } = useToolsPicker(initialToolConfig);
  /** Internal artifact store — synced to parent via callbacks */
  const [internalArtifacts, setInternalArtifacts] = useState<Artifact[]>(initialArtifacts || []);
  // Use external artifacts if they have entries, otherwise fall back to internal.
  // Note: `[] || x` evaluates to `[]` because empty arrays are truthy in JS.
  // The parent only holds artifacts loaded with the page or opened in the
  // panel; this chat holds everything streamed since. Merge them, or a card
  // whose artifact only one side knows drops out (and its source shows raw).
  const allArtifacts = useMemo(() => {
    if (!externalArtifacts?.length) return internalArtifacts;
    const byId = new Map(internalArtifacts.map((a) => [a.id, a]));
    for (const a of externalArtifacts) byId.set(a.id, a);
    return [...byId.values()];
  }, [externalArtifacts, internalArtifacts]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const queuedMessagesRef = useRef<{ text: string; files?: ChatFile[] }[]>([]);
  // Text of a send that has started but may not have re-rendered yet: a second
  // Enter/click in the same render sees a stale `isStreaming`/`input`.
  const sendInFlightRef = useRef<string | null>(null);
  // Plan mode: the agent researches read-only and proposes a plan to review.
  // Plan mode is remembered per chat. Toggling writes it; a new chat's choice is
  // copied to its id when the id is created (handleSend); opening a chat reads it.
  const [planMode, setPlanModeState] = useState(false);
  const planKey = planModeKey(conversationId);
  const planKeyRef = useRef(planKey);
  planKeyRef.current = planKey;
  const setPlanMode = useCallback((value: boolean | ((on: boolean) => boolean)) => {
    setPlanModeState((on) => {
      const next = typeof value === "function" ? value(on) : value;
      writePlanMode(planKeyRef.current, next);
      return next;
    });
  }, []);
  useEffect(() => {
    setPlanModeState(readPlanMode(planKey));
  }, [planKey]);
  // Slash commands: highlighted row, and the input the menu was dismissed at.
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissedAt, setSlashDismissedAt] = useState<string | null>(null);
  const { templates: promptTemplates } = usePromptTemplates();
  const handleSendRef = useRef<
    ((message?: string, opts?: { fromQueue?: boolean; includePendingFiles?: boolean; files?: ChatFile[] }) => Promise<void>) | null
  >(null);
  const [queuedCount, setQueuedCount] = useState(0);
  const [editingQueuedIndex, setEditingQueuedIndex] = useState<number | null>(null);
  const [editingQueuedText, setEditingQueuedText] = useState("");
  const editingQueuedIndexRef = useRef<number | null>(null);

  // Tracks whether the user is pinned to (near) the bottom of the message list.
  // When they scroll up to read earlier messages, streaming auto-scroll is
  // suppressed so the view doesn't yank them back down.
  const isAtBottomRef = useRef(true);
  const handleMessagesScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    isAtBottomRef.current = distanceFromBottom < 120;
  }, []);

  // By default auto-scroll only follows when the user is already at the bottom.
  // Pass { force: true } for user-initiated actions (e.g. sending a message)
  // that should always snap to the latest content.
  const scrollToBottom = useCallback((opts?: { force?: boolean }) => {
    if (!opts?.force && !isAtBottomRef.current) return;
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 50);
  }, []);

  useEffect(() => {
    if (initialItems?.length) {
      setItems(initialItems);
    }
  }, [initialItems]);

  // Opening an existing conversation lands at the latest message, not the top.
  const didInitialScrollRef = useRef(false);
  useEffect(() => {
    if (didInitialScrollRef.current || items.length === 0) return;
    didInitialScrollRef.current = true;
    messagesEndRef.current?.scrollIntoView({ behavior: "instant" });
  }, [items]);

  useEffect(() => {
    if (initialConversationId) {
      setConversationId(initialConversationId);
    }
  }, [initialConversationId]);

  useEffect(() => {
    if (autoFocusComposer) inputRef.current?.focus();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Restore an unsent draft (e.g. the task drawer was closed by mistake).
  // Server-provided prompts (staged board drafts) win over the local draft.
  // Moving to another chat's key (same panel, new ?continue=) shows that
  // chat's draft instead of carrying this one's text over.
  const draftKeyRef = useRef(draftStorageKey);
  const skipDraftSaveRef = useRef(false);
  useEffect(() => {
    const switched = draftKeyRef.current !== draftStorageKey;
    draftKeyRef.current = draftStorageKey;
    // The save below runs in this same commit with the old chat's text.
    if (switched) skipDraftSaveRef.current = true;
    if (!draftStorageKey || initialPrompt) return;
    try {
      const saved = window.localStorage.getItem(draftStorageKey);
      if (switched) setInput(saved || "");
      else if (saved) setInput((prev) => prev || saved);
    } catch {
      // localStorage unavailable (private mode) — drafts just don't persist.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftStorageKey]);

  // Persist unsent composer text; sending (or clearing) removes the draft.
  useEffect(() => {
    if (skipDraftSaveRef.current) {
      skipDraftSaveRef.current = false;
      return;
    }
    if (!draftStorageKey) return;
    try {
      if (input.trim()) window.localStorage.setItem(draftStorageKey, input);
      else window.localStorage.removeItem(draftStorageKey);
    } catch {
      // Ignore quota/private-mode failures.
    }
  }, [input, draftStorageKey]);

  // Write through during typing as well as in the effect above. This keeps a
  // draft safe even when the drawer is closed immediately after the last key.
  const updateComposerInput = (value: string) => {
    setInput(value);
    setSlashIndex(0);
    if (!draftStorageKey) return;
    try {
      if (value.trim()) window.localStorage.setItem(draftStorageKey, value);
      else window.localStorage.removeItem(draftStorageKey);
    } catch {
      // Ignore quota/private-mode failures.
    }
  };

  useEffect(() => {
    if (!isStreaming) {
      setItems((prev) => removeTransientStatusItems(prev));
    }
  }, [isStreaming]);

  useEffect(() => {
    if (!isStreaming || streamStartedAt === null) {
      setStreamElapsedSeconds(0);
      return;
    }

    const tick = () => {
      const raw = (performance.now() - streamStartedAt) / 1000;
      setStreamElapsedSeconds(Math.max(0, Math.round(raw * 10) / 10));
    };
    tick();
    const interval = window.setInterval(tick, 100);
    return () => window.clearInterval(interval);
  }, [isStreaming, streamStartedAt]);

  const adjustTextareaHeight = useCallback(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const maxHeight = 160;
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, []);

  useEffect(() => {
    adjustTextareaHeight();
  }, [input, isMobile, adjustTextareaHeight]);

  const addFiles = useCallback(async (fileList: FileList | File[]) => {
    const { files: chatFiles, rejected } = await filesToChatFiles(fileList);
    if (chatFiles.length) {
      setPendingFiles((prev) => [...prev, ...chatFiles]);
    }
    if (rejected.length) {
      console.warn("Unsupported files skipped:", rejected);
    }
  }, []);

  const removeFile = useCallback((index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleStop = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    if (!conversationId) {
      if (isRecovering) {
        setIsRecovering(false);
        setIsStreaming(false);
        setStreamStartedAt(null);
      }
      return;
    }
    // Ask the server to abort the runtime turn. While recovering, keep
    // polling: the poller ends once the server records the stop.
    interruptAgent(conversationId).catch((error) => {
      const message = error instanceof Error ? error.message : "Unknown error";
      if (isRecovering) {
        setIsRecovering(false);
        setIsStreaming(false);
        setStreamStartedAt(null);
      }
      setItems((prev) => [
        ...prev,
        { role: "assistant", content: `Could not stop the agent: ${message}` },
      ]);
    });
  }, [isRecovering, conversationId]);

  // Voice mode: voice talks, this chat's agent does the work.
  const voiceAvailable = useVoiceAvailable();
  const handleStopRef = useRef(handleStop);
  const voiceStateRef = useRef<ChatVoiceState>({ running: false, startedAt: null, currentStep: null, latestReply: null, waitingForUser: false });
  voiceStateRef.current = chatVoiceState(items, isStreaming, streamStartedAt);
  const voiceItemsRef = useRef(items);
  voiceItemsRef.current = items;
  const voiceChat = useMemo(() => ({
    getState: () => voiceStateRef.current,
    getMessages: () => chatVoiceMessages(voiceItemsRef.current),
    send: (message: string) => { void handleSendRef.current?.(message); },
    stop: () => handleStopRef.current(),
    conversationId: () => voiceConversationRef.current ?? null,
  }), []);
  // On the tasks board, a task opened while board voice is on takes the session over.
  const voice = useVoiceDispatcher({ chat: voiceChat, takeOverFromBoard: !readOnly, model: selectedModel || undefined, onBoardChanged: noop });
  // Opening a different chat ends voice; the new chat getting its id does not.
  const voiceConversationRef = useRef(conversationId);
  const stopVoice = voice.stop;
  useEffect(() => {
    if (initialConversationId && initialConversationId !== voiceConversationRef.current) stopVoice();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialConversationId]);
  voiceConversationRef.current = conversationId;

  useEffect(() => {
    const handleEscapeKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isStreaming && document.activeElement !== inputRef.current) {
        e.preventDefault();
        handleStop();
      }
    };
    document.addEventListener("keydown", handleEscapeKey);
    return () => document.removeEventListener("keydown", handleEscapeKey);
  }, [isStreaming, handleStop]);

  // Recovery polling: when stream breaks or page refreshes into a running
  // conversation, poll until complete. On each poll we rebuild items from
  // the server-side turns so intermediate steps show up progressively.
  useEffect(() => {
    if (!isRecovering || !conversationId) return;
    let stopped = false;

    const poll = async () => {
      try {
        const data = await fetchConversation(conversationId);
        if (stopped) return;

        // Rebuild items from turns on every poll so new steps appear,
        // but preserve any locally-queued messages the user added.
        // Pass the artifacts too, or the rebuilt messages lose their cards.
        const { items: rebuilt, artifacts: restored } = rebuildItemsFromConversation(
          data.conversation.messages,
          data.conversation.prompt,
          data.conversation.final_response,
          data.turns,
          data.artifacts,
        );
        if (restored.length) {
          setInternalArtifacts((prev) => {
            const byId = new Map(prev.map((a) => [a.id, a]));
            for (const a of restored) if (!byId.has(a.id)) byId.set(a.id, a);
            return [...byId.values()];
          });
        }
        const waitingForDeploy = data.conversation.status === DEPLOY_QUEUED_STATUS;
        setItems((prev) => {
          const queued = prev.filter((item) => item.queued);
          const base = waitingForDeploy ? markLatestUserDeployQueued(rebuilt, true) : rebuilt;
          return queued.length > 0 ? [...base, ...queued] : base;
        });
        scrollToBottom();

        // A run whose heartbeat stopped long ago is dead even if its status
        // still says running (crash/OOM). Stop waiting instead of showing
        // "Still working on your request..." forever.
        const deadRun = isDeadRun(data.conversation.status, data.conversation.last_heartbeat);
        if ((data.conversation.status !== "running" || deadRun) && !waitingForDeploy) {
          setIsRecovering(false);
          setIsStreaming(false);
          setItems((prev) => withTerminalStatus(prev, deadRun ? { status: "interrupted" } : data.conversation));
          onStreamComplete?.(conversationId);
          // Messages queued while the run was going are sent now, one run at a time.
          const queued = queuedMessagesRef.current;
          if (queued.length > 0) {
            queuedMessagesRef.current = [];
            setQueuedCount(0);
            setItems((prev) => prev.map((item) => (item.queued ? { ...item, queued: false } : item)));
            const combinedFiles = queued.flatMap((q) => q.files || []);
            const combinedText = queued.map((q) => q.text).join("\n\n");
            requestAnimationFrame(() => handleSendRef.current?.(combinedText, { fromQueue: true, files: combinedFiles }));
          } else {
            requestAnimationFrame(() => inputRef.current?.focus());
          }
          return;
        }
      } catch {
        // Ignore polling errors, will retry
      }
      if (!stopped) setTimeout(poll, 3000);
    };

    poll();
    return () => { stopped = true; };
  }, [isRecovering, conversationId, scrollToBottom, onStreamComplete]);

  // If page loaded with a running conversation (refresh), enter recovery mode
  useEffect(() => {
    if ((initialStatus === "running" || initialStatus === DEPLOY_QUEUED_STATUS) && initialConversationId) {
      setIsRecovering(true);
      setIsStreaming(true);
    }
  }, [initialStatus, initialConversationId]);

  const autoSendFired = useRef(false);
  useEffect(() => {
    // Wait for the model catalog so the send uses the intended model (e.g. a
    // board task's chosen model) instead of racing the default.
    if (autoSend && initialPrompt && !autoSendFired.current && !isStreaming && session
        && modelLoadState !== "loading") {
      autoSendFired.current = true;
      setInput("");
      // Call directly — a deferred setTimeout gets cancelled by this effect's
      // own cleanup when setInput triggers a re-render (no dep array), which
      // made auto-send silently flaky. Include pending files so attachments
      // staged with a board-task draft ride the auto-sent first message.
      handleSend(initialPrompt, { includePendingFiles: true });
    }
  });

  const handleSend = async (
    overrideMessage?: string,
    {
      fromQueue,
      includePendingFiles,
      files: queuedFiles,
      planMode: planModeOverride,
    }: { fromQueue?: boolean; includePendingFiles?: boolean; files?: ChatFile[]; planMode?: boolean } = {},
  ) => {
    // A plan-mode command (/plan) turns plan mode on, and keeps it on for
    // the review that follows, as the Plan toggle does.
    const commandPlansFirst = !!matchCommand(promptTemplates, (overrideMessage ?? input).trim())?.plan_mode;
    if (commandPlansFirst && planModeOverride === undefined) setPlanMode(true);
    const sendInPlanMode = planModeOverride ?? (planMode || commandPlansFirst);
    const displayText = overrideMessage ?? input.trim();
    // Files that rode a queued message are sent with it, not re-read from the composer.
    const carriedFiles = queuedFiles && queuedFiles.length > 0 ? queuedFiles : undefined;
    if (!displayText && pendingFiles.length === 0 && !carriedFiles) return;
    // Double Enter/click before re-render: same text, already on its way.
    if (!isStreaming && sendInFlightRef.current === displayText) return;

    if (isStreaming || sendInFlightRef.current !== null) {
      const isOverride = overrideMessage !== undefined;
      const filesToQueue = carriedFiles ?? (!isOverride && pendingFiles.length > 0 ? [...pendingFiles] : undefined);
      const fileNames = filesToQueue?.map((f) => f.name);
      const hasFiles = filesToQueue && filesToQueue.length > 0;

      // Show message immediately
      setItems((prev) => [
        ...prev,
        {
          role: "user", content: displayText, fileNames, files: filesToQueue,
          queued: !conversationId || hasFiles,
          replyAgentName: selectedAgent?.name || DEFAULT_AGENT_NAME,
          replyAgentId: selectedAgent?.agent_id ?? null,
        },
      ]);
      if (!isOverride) {
        setInput("");
        setPendingFiles([]);
        requestAnimationFrame(() => {
          if (inputRef.current) inputRef.current.style.height = "auto";
        });
      }
      isAtBottomRef.current = true;
      scrollToBottom({ force: true });

      // Try mid-stream injection (text-only; files fall back to queue)
      if (conversationId && !hasFiles) {
        try {
          await injectMessage(conversationId, displayText);
          return;
        } catch {
          // Stream likely ended — fall back to queue-and-send
        }
      }
      // Fallback: queue for delivery after current stream ends
      queuedMessagesRef.current.push({ text: displayText, files: filesToQueue });
      setQueuedCount(queuedMessagesRef.current.length);
      setItems((prev) =>
        prev.map((item, i) =>
          i === prev.length - 1 && !item.queued ? { ...item, queued: true } : item
        )
      );
      return;
    }
    const message = systemContext && overrideMessage
      ? `[Context: ${systemContext}]\n\n${displayText}`
      : displayText;

    const isOverride = overrideMessage !== undefined;
    const filesToSend = carriedFiles ?? ((!isOverride || includePendingFiles) && pendingFiles.length > 0
      ? [...pendingFiles]
      : undefined);
    const fileNames = filesToSend?.map((f) => f.name);
    const displayMessage = displayText || `[${fileNames?.join(", ")}]`;

    const history = buildHistory(items);
    const responseStartedAt = performance.now();

    // Sent: drop the draft now. A new chat's key changes to its id in the same
    // render, which skips the usual clean-up.
    if (draftStorageKey && !isOverride) {
      try { window.localStorage.removeItem(draftStorageKey); } catch { /* private mode */ }
    }

    // Generate conversation ID client-side for new conversations so the URL
    // is updated immediately (before the SSE stream establishes).
    let activeConversationId = conversationId;
    if (!activeConversationId) {
      activeConversationId = createConversationId();
      // The new chat's plan mode becomes this chat's; the next new chat starts fresh.
      writePlanMode(planModeKey(activeConversationId), planMode);
      writePlanMode(planModeKey(undefined), false);
      setConversationId(activeConversationId);
      window.history.replaceState(null, "", `${basePath}/chat?continue=${activeConversationId}`);
      onConversationCreated?.(activeConversationId);
    }

    if (!isOverride) {
      setInput("");
      setPendingFiles([]);
      setAccountInfo(null);
    } else if (includePendingFiles && filesToSend) {
      setPendingFiles([]);
    }
    if (!fromQueue) {
      setItems((prev) => [...prev, {
        role: "user", content: displayMessage, fileNames, files: filesToSend,
        replyAgentName: selectedAgent?.name || DEFAULT_AGENT_NAME,
        replyAgentId: selectedAgent?.agent_id ?? null,
      }]);
      // User sent a message — always snap down and resume following the stream.
      isAtBottomRef.current = true;
      scrollToBottom({ force: true });
    }
    setIsStreaming(true);
    setStreamStartedAt(performance.now());
    sendInFlightRef.current = displayText;

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    if (!isOverride) {
      requestAnimationFrame(() => {
        if (inputRef.current) {
          inputRef.current.style.height = "auto";
        }
      });
    }

    let enteredRecovery = false;

    try {
      for await (const event of streamChat(
        message,
        history,
        filesToSend,
        activeConversationId,
        session?.user?.email ?? undefined,
        abortController.signal,
        selectedModel || undefined,
        // Until the agent list loads, "no selection" is not yet a choice of the
        // default agent, so it must not unpin the conversation's agent.
        selectedAgentId || (agentLoadState === "ready" ? null : undefined),
        selectedAgentId ? AGENT_TOOL_CONFIG : toolConfig,
        sendInPlanMode,
      )) {
        if (event.type === "account_info") {
          setAccountInfo(event);
          continue;
        }
        if (event.type === "conversation_id") {
          // Server confirmed the ID — keep URL in sync in case it differs
          if (event.conversation_id !== activeConversationId) {
            setConversationId(event.conversation_id);
            window.history.replaceState(null, "", `${basePath}/chat?continue=${event.conversation_id}`);
          }
          continue;
        }
        if (event.type === "artifact") {
          const artifact: Artifact = {
            id: event.artifact_id,
            title: event.title,
            content: event.content,
            language: event.language,
            version: event.version,
            timestamp: Date.now(),
          };
          setInternalArtifacts((prev) => {
            // Replace if same id exists (update), otherwise append
            const existing = prev.findIndex((a) => a.id === artifact.id);
            if (existing >= 0) {
              const updated = [...prev];
              updated[existing] = artifact;
              return updated;
            }
            return [...prev, artifact];
          });
          // Add artifact reference to the current assistant message
          setItems((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              const existingIds = last.artifactIds || [];
              updated[updated.length - 1] = {
                ...last,
                artifactIds: existingIds.includes(artifact.id)
                  ? existingIds
                  : [...existingIds, artifact.id],
              };
            } else {
              // Create a new assistant item with just the artifact reference
              updated.push({
                role: "assistant",
                content: "",
                artifactIds: [artifact.id],
              });
            }
            return updated;
          });
          // Auto-open the artifact in the viewer
          if (onArtifactOpen) {
            onArtifactOpen(artifact);
          }
          scrollToBottom();
          continue;
        }
        if (event.type === "file_artifact") {
          const artifact: Artifact = {
            id: event.artifact_id,
            title: event.title,
            content: "", // File artifacts have no inline text content
            language: event.language,
            version: event.version,
            timestamp: Date.now(),
            file_url: event.file_url,
            file_size: event.file_size,
            file_type: event.file_type,
            previews: event.previews,
          };
          setInternalArtifacts((prev) => {
            const existing = prev.findIndex((a) => a.id === artifact.id);
            if (existing >= 0) {
              const updated = [...prev];
              updated[existing] = artifact;
              return updated;
            }
            return [...prev, artifact];
          });
          // Add file artifact reference to the current assistant message
          setItems((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              const existingIds = last.artifactIds || [];
              updated[updated.length - 1] = {
                ...last,
                artifactIds: existingIds.includes(artifact.id)
                  ? existingIds
                  : [...existingIds, artifact.id],
              };
            } else {
              updated.push({
                role: "assistant",
                content: "",
                artifactIds: [artifact.id],
              });
            }
            return updated;
          });
          // Auto-open the file artifact in the viewer
          if (onArtifactOpen) {
            onArtifactOpen(artifact);
          }
          scrollToBottom();
          continue;
        }
        if (event.type === "file") {
          const fileAttachment: FileAttachment = {
            file_id: event.file_id,
            name: event.name,
            url: event.url,
            mime_type: event.mime_type,
            size: event.size,
          };
          // Attach to the current assistant message
          setItems((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              const existing = last.fileAttachments || [];
              // Avoid duplicates
              if (!existing.some((f) => f.file_id === fileAttachment.file_id)) {
                updated[updated.length - 1] = {
                  ...last,
                  fileAttachments: [...existing, fileAttachment],
                };
              }
            } else {
              // Create new assistant item with just the file
              updated.push({
                role: "assistant",
                content: "",
                fileAttachments: [fileAttachment],
              });
            }
            return updated;
          });
          scrollToBottom();
          continue;
        }
        setItems((prev) => applyEvent(prev, event));
        scrollToBottom();
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setItems((prev) => [
          ...prev,
          { role: "assistant", content: STOPPED_BY_USER_MESSAGE },
        ]);
      } else if (error instanceof ConversationQueuedError && activeConversationId) {
        // A deploy is in progress. The server saved the message and will run it
        // once the new version is up; follow the conversation until then.
        setItems((prev) => markLatestUserDeployQueued(prev, true));
        enteredRecovery = true;
        setIsRecovering(true);
      } else if (error instanceof ChatRequestError) {
        // Refused before anything was saved: give the text and files back so
        // nothing typed is lost, and drop the optimistic bubble.
        if (!fromQueue) {
          setItems((prev) => {
            const idx = prev.map((item) => item.role).lastIndexOf("user");
            return idx >= 0 ? prev.filter((_, i) => i !== idx) : prev;
          });
        }
        if (!isOverride) {
          setInput((current) => (current.trim() ? current : displayText));
          if (filesToSend?.length) setPendingFiles((current) => (current.length ? current : filesToSend));
        }
        setItems((prev) => [...prev, { role: "assistant", content: `Error: ${error.message}` }]);
      } else if (error instanceof ConversationBusyError && activeConversationId) {
        // A run is already going (another tab, a retry, a race). The server
        // injected it, dropped a duplicate, or we queue it for when the run
        // ends; either way follow the running run instead of starting one.
        if (error.duplicate) {
          setItems((prev) => {
            const idx = prev.map((item) => item.role).lastIndexOf("user");
            return idx >= 0 && !fromQueue ? prev.filter((_, i) => i !== idx) : prev;
          });
        } else if (!error.injected) {
          queuedMessagesRef.current.push({ text: displayText, files: filesToSend });
          setQueuedCount(queuedMessagesRef.current.length);
          setItems((prev) => {
            const idx = prev.map((item) => item.role).lastIndexOf("user");
            return prev.map((item, i) => (i === idx ? { ...item, queued: true } : item));
          });
        }
        enteredRecovery = true;
        setIsRecovering(true);
      } else if (activeConversationId) {
        // Stream broke but agent may still be running — enter recovery mode
        enteredRecovery = true;
        setIsRecovering(true);
        setItems((prev) => [
          ...prev,
          { role: "assistant", content: RECOVERY_MESSAGE },
        ]);
      } else {
        setItems((prev) => [
          ...prev,
          {
            role: "assistant",
            content: `Error: ${error instanceof Error ? error.message : "Unknown error"}`,
          },
        ]);
      }
    } finally {
      sendInFlightRef.current = null;
      const responseTimeSeconds = Math.max(0.1, (performance.now() - responseStartedAt) / 1000);
      setItems((prev) => {
        const finalized = removeTransientStatusItems(finalizeSteps(closeThinking(prev)));
        return enteredRecovery
          ? finalized
          : stampLatestAssistantDuration(finalized, responseTimeSeconds);
      });
      if (!enteredRecovery) {
        setIsStreaming(false);
        setStreamStartedAt(null);
        if (activeConversationId && onStreamComplete) {
          onStreamComplete(activeConversationId);
        }
      }
      abortControllerRef.current = null;
      scrollToBottom();

      const queued = queuedMessagesRef.current;
      if (queued.length > 0 && !enteredRecovery) {
        const editingIdx = editingQueuedIndexRef.current;
        if (editingIdx !== null) {
          let editQIdx = 0;
          for (let j = 0; j < editingIdx; j++) {
            if (items[j].queued) editQIdx++;
          }
          const kept = queued[editQIdx];
          const toSend = queued.filter((_, qi) => qi !== editQIdx);
          queuedMessagesRef.current = kept ? [kept] : [];
          setQueuedCount(queuedMessagesRef.current.length);
          setItems((prev) => prev.map((item, idx) =>
            item.queued && idx !== editingIdx ? { ...item, queued: false } : item
          ));
          if (toSend.length > 0) {
            const combinedText = toSend.map((q) => q.text).join("\n\n");
            const combinedFiles = toSend.flatMap((q) => q.files || []);
            requestAnimationFrame(() => handleSend(combinedText, { fromQueue: true, files: combinedFiles }));
          }
        } else {
          queuedMessagesRef.current = [];
          setQueuedCount(0);
          setItems((prev) => prev.map((item) =>
            item.queued ? { ...item, queued: false } : item
          ));
          const combinedText = queued.map((q) => q.text).join("\n\n");
          const combinedFiles = queued.flatMap((q) => q.files || []);
          requestAnimationFrame(() => handleSend(combinedText, { fromQueue: true, files: combinedFiles }));
        }
      } else if (!enteredRecovery) {
        requestAnimationFrame(() => {
          inputRef.current?.focus();
        });
      }
    }
  };

  handleSendRef.current = handleSend;
  handleStopRef.current = handleStop;

  const handleClarifySubmit = useCallback(
    (itemIndex: number, selectedLabels: string[], answer: string) => {
      setItems((prev) =>
        prev.map((item, i) =>
          i === itemIndex ? { ...item, submitted: true, selectedLabels } : item
        )
      );
      handleSend(answer);
    },
    [items, conversationId, session, isStreaming, selectedModel, selectedAgentId, agentLoadState],
  );

  const getQueueIndex = (itemIndex: number): number => {
    let qIdx = 0;
    for (let j = 0; j < itemIndex; j++) {
      if (items[j].queued) qIdx++;
    }
    return qIdx;
  };

  const handleDeleteQueued = (itemIndex: number) => {
    const qIdx = getQueueIndex(itemIndex);
    queuedMessagesRef.current.splice(qIdx, 1);
    setQueuedCount(queuedMessagesRef.current.length);
    setItems((prev) => prev.filter((_, i) => i !== itemIndex));
    if (editingQueuedIndex === itemIndex) {
      setEditingQueuedIndex(null);
      editingQueuedIndexRef.current = null;
      setEditingQueuedText("");
    }
  };

  const handleStartEditQueued = (itemIndex: number) => {
    setEditingQueuedIndex(itemIndex);
    editingQueuedIndexRef.current = itemIndex;
    setEditingQueuedText(items[itemIndex].content);
  };

  const handleSaveEditQueued = (itemIndex: number) => {
    const trimmed = editingQueuedText.trim();
    if (!trimmed) {
      handleDeleteQueued(itemIndex);
      return;
    }
    const qIdx = getQueueIndex(itemIndex);
    queuedMessagesRef.current[qIdx] = { ...queuedMessagesRef.current[qIdx], text: trimmed };
    setItems((prev) =>
      prev.map((item, i) => (i === itemIndex ? { ...item, content: trimmed } : item))
    );
    setEditingQueuedIndex(null);
    editingQueuedIndexRef.current = null;
    setEditingQueuedText("");
  };

  const handleCancelEditQueued = () => {
    setEditingQueuedIndex(null);
    editingQueuedIndexRef.current = null;
    setEditingQueuedText("");
  };

  const visibleSlashMatches = slashDismissedAt === input ? [] : slashMatches(promptTemplates, input);
  const activeCommand = matchCommand(promptTemplates, input);
  const applyCommand = (template: { name: string }) => {
    updateComposerInput(`/${template.name} `);
    setSlashIndex(0);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (visibleSlashMatches.length) {
      const count = visibleSlashMatches.length;
      const index = Math.min(slashIndex, count - 1);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((index + (e.key === "ArrowDown" ? 1 : count - 1)) % count);
        return;
      }
      if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Tab" && !e.shiftKey)) {
        e.preventDefault();
        applyCommand(visibleSlashMatches[index]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashDismissedAt(input);
        return;
      }
    }
    // Shift+Tab toggles plan mode, as in Claude Code.
    if (e.key === "Tab" && e.shiftKey && !readOnly) {
      e.preventDefault();
      setPlanMode((on) => !on);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const pasted = filesFromClipboard(e.clipboardData);
      if (pasted.length > 0) {
        e.preventDefault();
        addFiles(pasted);
      }
    },
    [addFiles],
  );

  const { isDragOver, dropHandlers } = useFileDrop(addFiles);

  // Plans in chat order: number them v1, v2, ... and work out which one is
  // under review. Approval is the user's "Plan vN approved" message, so the
  // status survives a reload without extra storage.
  const planState = useMemo<PlanState>(() => {
    const plans: Artifact[] = [];
    const planItemIndex: number[] = [];
    items.forEach((item, i) => {
      for (const id of item.artifactIds || []) {
        const art = allArtifacts.find((a) => a.id === id);
        if (art?.language === "plan" && !plans.some((p) => p.id === id)) {
          plans.push({ ...art, version: plans.length + 1 });
          planItemIndex.push(i);
        }
      }
    });
    const statuses: Record<string, PlanStatus> = {};
    plans.forEach((plan, n) => {
      const approved = items
        .slice(planItemIndex[n] + 1)
        .some((item) => item.role === "user" && item.content.startsWith(`Plan v${plan.version} approved`));
      statuses[plan.id] = approved ? "approved" : n < plans.length - 1 ? "superseded" : "pending";
    });
    return { plans, statuses, canAct: !isStreaming && !readOnly };
  }, [items, allArtifacts, isStreaming, readOnly]);
  // allArtifacts is rebuilt each render, so report only real changes.
  const planStateKey = JSON.stringify([planState.plans.map((p) => p.id), planState.statuses, planState.canAct]);
  const planStateRef = useRef(planState);
  planStateRef.current = planState;
  useEffect(() => {
    onPlanStateChange?.(planStateRef.current);
  }, [planStateKey, onPlanStateChange]);
  useEffect(() => {
    if (!planControlRef) return;
    planControlRef.current = {
      revise: (plan, comments) => {
        setPlanMode(true);
        handleSend(revisionMessage(plan, comments), { planMode: true });
      },
      approve: (plan, notes) => {
        setPlanMode(false);
        handleSend(approvalMessage(plan, notes), { planMode: false });
      },
    };
  });

  const isEmptyState = items.length === 0 && !isStreaming;

  // A selected agent brings its own tools and skills, so the pickers give way
  // to a read-only summary of that scope.
  const scopePicker = selectedAgent ? (
    <AgentScope agent={selectedAgent} skills={availableSkills} onOpen={loadToolsCatalog} onUseDefault={() => selectAgent(null)} disabled={isStreaming} />
  ) : (
    <ToolsPicker tools={availableTools} skills={availableSkills} selection={toolsSelection} onSetEnabled={setEnabled} onSetAll={setAll} onOpen={loadToolsCatalog} loadState={toolsLoadState} disabled={isStreaming} />
  );

  // A mode, not a stray button: do the task, or plan it first (Shift+Tab switches).
  const planToggle = (
    <div
      role="radiogroup"
      aria-label="Mode"
      title="Plan first: research read-only, then review a plan before anything changes (Shift+Tab)"
      className="flex shrink-0 rounded-lg bg-muted p-0.5 text-xs"
    >
      <button
        type="button"
        role="radio"
        aria-checked={!planMode}
        onClick={() => setPlanMode(false)}
        className={cn("h-7 rounded-md px-2.5 transition-colors", !planMode ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}
      >
        Do it
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={planMode}
        onClick={() => setPlanMode(true)}
        className={cn("flex h-7 items-center gap-1 rounded-md px-2.5 transition-colors", planMode ? "bg-brand-700 font-medium text-brand-50 shadow-sm" : "text-muted-foreground hover:text-foreground")}
      >
        <RiListCheck3 size={13} />
        Plan first
      </button>
    </div>
  );
  // While plan mode is on, the composer says what will happen.
  const planBanner = planMode && !readOnly && (
    <div role="status" className="mb-1.5 flex items-start gap-2 rounded-xl border border-brand-200 bg-brand-50 px-3 py-2 text-[13px] leading-snug text-brand-700">
      <RiListCheck3 size={15} className="mt-px shrink-0" />
      <span><b className="font-semibold">Plan first.</b> Loma researches read-only, then shows you a plan with diagrams. Nothing changes until you approve it.</span>
    </div>
  );
  const planPlaceholder = "What should Loma plan? It researches first and changes nothing";

  // Shared by the empty-state and in-conversation composers.
  const composerPickers = (
    <>
      {isMobile && (
        <Button type="button" variant="ghost" size="icon" onClick={() => fileInputRef.current?.click()} aria-label="Attach files" className="size-11 shrink-0 rounded-full text-muted-foreground">
          <RiAttachmentLine size={18} />
        </Button>
      )}
      {!isMobile && <AgentPicker agents={agentIdentities} selectedAgentId={selectedAgentId} onSelect={selectAgent} loadState={agentLoadState} disabled={isStreaming} />}
      <div className="min-w-0 max-md:flex-1 max-md:[&_button]:h-11"><ModelPicker models={agentModels} selectedModel={selectedModel} onSelect={selectModel} loadState={modelLoadState} disabled={isStreaming} /></div>
      {isMobile ? (
        <ComposerSettings>
          <AgentPicker agents={agentIdentities} selectedAgentId={selectedAgentId} onSelect={selectAgent} loadState={agentLoadState} disabled={isStreaming} />
          {scopePicker}
          {planToggle}
        </ComposerSettings>
      ) : (
        <>
          {scopePicker}
          {planToggle}
        </>
      )}
    </>
  );

  // Inside a conversation on a phone the composer is one row: attach, model,
  // agent, tools and skills all sit behind "+" so the reading area gets the
  // toolbar row back. The new-chat screen keeps its fuller composer.
  const conversationPlus = (
    <ComposerSettings trigger="plus" title="Chat options" description="Attach files, or choose the model, agent, tools and skills." onAttach={() => fileInputRef.current?.click()}>
      <ModelPicker models={agentModels} selectedModel={selectedModel} onSelect={selectModel} loadState={modelLoadState} disabled={isStreaming} />
      <AgentPicker agents={agentIdentities} selectedAgentId={selectedAgentId} onSelect={selectAgent} loadState={agentLoadState} disabled={isStreaming} />
      {scopePicker}
      {planToggle}
    </ComposerSettings>
  );

  const messageBlocks = groupIntoBlocks(items);

  // Before the first event arrives, give the working line an agent block to sit in.
  if (isStreaming && !isRecovering && messageBlocks[messageBlocks.length - 1]?.kind !== "agent") {
    messageBlocks.push({ kind: "agent", indices: [] });
  }
  const lastItem = items[items.length - 1];
  const latestStatus = lastItem?.role === "status" ? lastItem.content : undefined;
  const accountSummary = describeAccount(accountInfo);

  return (
    <div
      className="flex flex-col h-full"
      {...dropHandlers}
    >
      {/* Lightbox overlay */}
      {expandedImage && (
        <ImageLightbox src={expandedImage} onClose={() => setExpandedImage(null)} />
      )}

      {/* Drag overlay */}
      {isDragOver && (
        <div className="absolute inset-0 bg-brand-50/80 border-2 border-dashed border-brand-400 rounded-xl z-50 flex items-center justify-center">
          <div className="text-brand-600 font-medium text-[13px] flex items-center gap-2">
            <RiUploadLine size={20} />
            Drop files here
          </div>
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) {
            addFiles(e.target.files);
            e.target.value = "";
          }
        }}
      />

      {isEmptyState ? (
        /* Empty state */
        <div className={cn("flex min-h-0 flex-col items-center justify-center h-full px-1 md:px-6 animate-fade-in-up", keyboardOpen && "max-md:justify-end")}>
          <div className={cn("mb-7 md:mb-8 flex flex-col items-center gap-4 text-center", keyboardOpen && "max-md:hidden")}>
            <PetCompanion size={56} />
            <h2 className="editorial-heading text-[26px] md:text-[34px] text-foreground">
              {readOnly ? (
                "This task hasn't started yet"
              ) : (
                <>
                  <span className="md:hidden text-[34px] leading-[1.1]">A little help.<br />A lot done.</span>
                  <span className="hidden md:inline">What do you need to get done?</span>
                </>
              )}
            </h2>
          </div>

          <div className="w-full max-w-full md:max-w-[720px]">
            {readOnly ? <ReadOnlyComposerNotice /> : <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
            >
              <PendingFilesStrip files={pendingFiles} onRemove={removeFile} onExpandImage={setExpandedImage} />
              {voice.active
                ? <VoicePanel voice={voice} className="mb-1.5 rounded-2xl border border-border bg-card" />
                : voice.error && <p className="mb-1 text-xs text-destructive">{voice.error}</p>}

              <div className="relative">
              {activeCommand && !visibleSlashMatches.length && <SlashCommandHint template={activeCommand} />}
              <SlashCommandMenu
                matches={visibleSlashMatches}
                activeIndex={Math.min(slashIndex, Math.max(visibleSlashMatches.length - 1, 0))}
                onSelect={applyCommand}
                onHover={setSlashIndex}
              />
              {planBanner}
              <div className={cn("relative flex flex-col bg-card border border-border rounded-2xl composer-shadow focus-within:border-input transition-colors", planMode && "border-brand-400 focus-within:border-brand-500")}>
                <Textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => updateComposerInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  onFocus={() => {
                    // Phones: the keyboard shrinks --app-h; keep the latest
                    // message in view above the composer.
                    if (standalone || isMobile) scrollToBottom();
                  }}
                  placeholder={isStreaming ? "Type your next message..." : planMode ? planPlaceholder : isMobile ? "Ask Loma anything..." : "What do you need to get done? Type / for commands"}
                  rows={isMobile ? 1 : 2}
                  className="w-full bg-transparent px-4 md:px-5 pt-4 md:pt-5 pb-3 text-base md:text-[16px] text-foreground placeholder-muted-foreground focus:outline-none resize-none overflow-hidden leading-relaxed border-0 focus-visible:ring-0 focus-visible:border-transparent rounded-none min-h-0"
                  style={{ maxHeight: "200px" }}
                />
                {/* In a narrow panel (task drawer, beside a plan) the controls wrap to a second line instead of overlapping. */}
                <div className="flex items-center justify-between gap-2 px-3 pb-3 max-md:gap-0 max-md:px-2 max-md:pb-2 md:flex-wrap md:justify-start md:gap-x-1 md:gap-y-1.5">
                  <div className="flex min-w-0 items-center gap-0.5 max-md:flex-1 md:contents">
                    {composerPickers}
                  </div>
                  <div className="ml-auto flex items-center gap-1 max-md:gap-0 shrink-0">
                    {/* Dictation fills the box; voice mode is a live conversation. Voice replaces the mic while on. */}
                    {!voice.active && <DictationButton
                      onText={(t) => setInput((prev) => appendDictation(prev, t))}
                      mobileProminent
                      hideIdleOnMobile={!!input.trim() || pendingFiles.length > 0}
                      compactMobile
                    />}
                    {!voice.active && voiceAvailable && (
                      <VoiceModeButton voice={voice} className={cn((!!input.trim() || pendingFiles.length > 0) && "max-md:hidden")} />
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => fileInputRef.current?.click()}
                      title="Attach files"
                      className="text-muted-foreground hover:text-foreground max-md:hidden"
                    >
                      <RiAttachmentLine size={16} />
                    </Button>
                    <Button
                      type="submit"
                      disabled={!input.trim() && pendingFiles.length === 0}
                      className={cn(
                        "bg-primary text-primary-foreground hover:bg-accent-200 hover:text-accent-on disabled:opacity-40 disabled:hover:bg-primary disabled:hover:text-primary-foreground rounded-lg press-scale max-md:size-11 max-md:rounded-full",
                        !input.trim() && pendingFiles.length === 0 && "max-md:hidden",
                      )}
                      size="icon-sm"
                    >
                      <span className="sr-only">{planMode ? "Make a plan" : "Send message"}</span>
                      <RiSendPlaneLine size={16} />
                    </Button>
                  </div>
                </div>
              </div>
              </div>
            </form>}
          </div>
        </div>
      ) : (
        /* Normal chat layout */
        <>
          {/* Messages */}
          <div data-slot="chat-messages" className="flex-1 overflow-y-auto overscroll-contain px-3 py-4" onScroll={handleMessagesScroll}>
            <div className="max-w-3xl mx-auto">
              {messageBlocks.map((block, blockIndex) => {
                if (block.kind === "user") {
                  const i = block.index;
                  const item = items[i];
                  return (
                    <div key={`u${i}`} data-chat-role="user" className="flex justify-end animate-message-in group/msg mt-6 first:mt-0">
                      {item.queued && editingQueuedIndex !== i && (
                        <div className="flex items-center gap-0.5 pointer-coarse:gap-1.5 mr-1.5 opacity-0 group-hover/msg:opacity-100 group-focus-within/msg:opacity-100 pointer-coarse:opacity-100 transition-opacity">
                          <button
                            type="button"
                            onClick={() => handleStartEditQueued(i)}
                            className="touch-target p-1 pointer-coarse:p-2 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                            title="Edit queued message"
                            aria-label="Edit queued message"
                          >
                            <RiEditLine size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteQueued(i)}
                            className="touch-target p-1 pointer-coarse:p-2 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
                            title="Delete queued message"
                            aria-label="Delete queued message"
                          >
                            <RiDeleteBinLine size={14} />
                          </button>
                        </div>
                      )}
                      <div className={cn(
                        "rounded-[20px] px-4 py-2.5 max-w-[88%] md:max-w-[75%] text-[16px] leading-[1.6] break-words whitespace-pre-wrap",
                        item.queued || item.deployQueued
                          ? "bg-card/60 border border-dashed border-border"
                          : "bg-muted text-foreground"
                      )}>
                        {multipleSenders && item.sender && (
                          <div className="mb-1 text-[13px] font-medium text-muted-foreground">{item.sender}</div>
                        )}
                        {editingQueuedIndex === i ? (
                          <div className="flex flex-col gap-1.5">
                            <textarea
                              value={editingQueuedText}
                              onChange={(e) => setEditingQueuedText(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && !e.shiftKey) {
                                  e.preventDefault();
                                  handleSaveEditQueued(i);
                                }
                                if (e.key === "Escape") {
                                  handleCancelEditQueued();
                                }
                              }}
                              className="w-full bg-transparent border-none outline-none resize-none text-base md:text-[16px] leading-relaxed min-h-[2em]"
                              autoFocus
                            />
                            <div className="flex items-center justify-end gap-1.5">
                              <button
                                type="button"
                                onClick={handleCancelEditQueued}
                                className="text-[13px] text-muted-foreground hover:text-foreground px-2 py-0.5 rounded transition-colors"
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={() => handleSaveEditQueued(i)}
                                className="text-[13px] text-primary hover:text-primary/80 px-2 py-0.5 rounded transition-colors font-medium"
                              >
                                Save
                              </button>
                            </div>
                          </div>
                        ) : (
                          item.content || <TypingIndicator />
                        )}
                        {item.queued && editingQueuedIndex !== i && (
                          <div className="flex items-center gap-1 mt-1.5 text-[13px] text-muted-foreground">
                            <RiTimeLine size={12} />
                            <span>Queued — will send when agent finishes</span>
                          </div>
                        )}
                        {item.deployQueued && !item.queued && (
                          <div className="flex items-center gap-1 mt-1.5 text-[13px] text-muted-foreground">
                            <RiTimeLine size={12} />
                            <span>Queued, Loma is updating. This will start as soon as it&apos;s back.</span>
                          </div>
                        )}
                        {item.fileNames && item.fileNames.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {item.fileNames.map((name, fi) => {
                              const fileData = item.files?.find((f) => f.name === name);
                              if (fileData && fileData.type === "image") {
                                const src = `data:${fileData.mimetype};base64,${fileData.data}`;
                                return (
                                  <button
                                    key={fi}
                                    type="button"
                                    onClick={() => setExpandedImage(src)}
                                    className="w-16 h-16 rounded-lg overflow-hidden border border-border hover:border-foreground/20 transition-colors flex-shrink-0"
                                  >
                                    <img src={src} alt={name} className="w-full h-full object-cover" />
                                  </button>
                                );
                              }
                              return (
                                <Badge key={fi} variant="secondary" className="gap-1 px-2 py-0.5 rounded-md">
                                  <RiAttachmentLine size={12} />
                                  {name}
                                </Badge>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                }

                // One agent reply: a single avatar, then its steps, notes and
                // answer stacked in one column.
                const isLastBlock = blockIndex === messageBlocks.length - 1;
                const showWorking = isStreaming && !isRecovering && isLastBlock;
                const firstAuthor = replyAuthor(block.indices[0] ?? items.length);
                const firstAgent = firstAuthor.agentId ? agentsById[firstAuthor.agentId] : undefined;
                const process = processIndices(items, block.indices);
                const activeThought = isStreaming ? process.findIndex((p) => items[p].role === "thinking"
                  && items[p].thinkingSeconds === undefined
                  && items.slice(p + 1).every((later) => later.role === "status")) : -1;
                let previousAuthorName: string | null = null;
                return (
                  <div key={`a${block.indices[0] ?? "w"}`} data-chat-role="assistant" className="flex justify-start items-start animate-message-in gap-2 mt-5 first:mt-0">
                    {firstAgent ? (
                      <AgentAvatar avatar={firstAgent.avatar} size={24} className="rounded-full shrink-0" />
                    ) : (
                      <PetCompanion size={24} fallback={<CrosscutIcon size={16} className="shrink-0 mt-px" />} />
                    )}
                    <div className="min-w-0 flex-1 space-y-2 pt-0.5">
                      {block.indices.map((i) => {
                        const item = items[i];
                        if (isProcessItem(item)) {
                          if (i !== process[0]) return null;
                          return (
                            <ProcessGroup
                              key={i}
                              items={process.map((p) => items[p])}
                              activeThought={activeThought}
                              working={showWorking}
                            />
                          );
                        }

                        if (item.role === "status") {
                          // While streaming, the working line carries the latest status.
                          return isStreaming ? null : <StatusPill key={i} message={item.content} elapsedSeconds={item.elapsedSeconds} />;
                        }

                        const author = replyAuthor(i);
                        const showAuthor = threadHasAgents && author.agentName !== previousAuthorName;
                        previousAuthorName = author.agentName;
                        const authorLabel = showAuthor && (
                          <div className="not-prose mb-1 text-[13px] font-semibold text-muted-foreground" data-testid="reply-agent">
                            {author.agentName}
                          </div>
                        );

                        if (item.role === "clarify") {
                          return (
                            <div key={i} className="min-w-0 text-[16px] leading-[1.65] break-words">
                              {authorLabel}
                              {item.content && (
                                <div data-chat-reply="" className="mb-3 [&>*:first-child]:mt-0">
                                  <MarkdownContent content={item.content} className="text-[16px]" />
                                </div>
                              )}
                              <ClarifyingQuestions
                                questions={item.questions || []}
                                submitted={item.submitted || false}
                                selectedLabels={item.selectedLabels}
                                onSubmit={(selected, otherText) =>
                                  handleClarifySubmit(i, selected, otherText)
                                }
                              />
                            </div>
                          );
                        }

                        // Assistant message — editorial style, no bubble
                        const itemArtifacts = (item.artifactIds || [])
                          .map((artId) => allArtifacts.find((a) => a.id === artId))
                          .filter((a): a is Artifact => !!a);
                        // A run that stopped or failed ends on a card with the next step.
                        const terminal = !isStreaming && !readOnly && i === items.length - 1 ? terminalKind(item.content) : null;
                        if (terminal) {
                          return (
                            <RunStoppedCard
                              key={i}
                              kind={terminal}
                              detail={terminal === "error" ? item.content.slice("Error: ".length) : null}
                              onContinue={() => handleSendRef.current?.(
                                terminal === "error" ? "That failed. Please try again." : "Continue where you left off.",
                              )}
                            />
                          );
                        }
                        const { text: displayText, pending, sections: planSectionsSoFar } = withoutArtifactSource(
                          item.content,
                          itemArtifacts,
                          isStreaming && i === items.length - 1,
                        );
                        return (
                          <div key={i} className="group/reply flex items-start gap-2">
                            <div className="min-w-0 flex-1 text-[16px] leading-[1.65] break-words [&>*:first-child]:mt-0">
                              {authorLabel}
                              {displayText ? (
                                <div data-chat-reply=""><MarkdownContent content={displayText} className="text-[16px]" /></div>
                              ) : (pending || item.artifactIds?.length || item.fileAttachments?.length) ? (
                                null /* Artifact/file-only message — cards rendered below */
                              ) : (
                                <TypingIndicator />
                              )}
                              {pending === "plan" && (
                                <div role="status" className={cn("flex w-full max-w-[420px] flex-col gap-1.5 rounded-2xl border border-dashed border-brand-300 bg-brand-50 px-4 py-3", displayText && "mt-3")}>
                                  <span className="text-[13px] font-semibold text-brand-700">Writing the plan</span>
                                  {planSectionsSoFar.length === 0 ? (
                                    <span className="flex items-center gap-2 text-[14px] text-muted-foreground">
                                      <RiLoader4Line size={14} className="animate-spin text-brand-600" /> Summary
                                    </span>
                                  ) : planSectionsSoFar.map((name, n) => (
                                    <span key={`${n}-${name}`} className="flex items-center gap-2 text-[14px]">
                                      {n < planSectionsSoFar.length - 1
                                        ? <RiCheckLine size={14} className="text-brand-600" />
                                        : <RiLoader4Line size={14} className="animate-spin text-brand-600" />}
                                      <span className={n < planSectionsSoFar.length - 1 ? "text-foreground" : "font-medium text-foreground"}>{name}</span>
                                    </span>
                                  ))}
                                </div>
                              )}
                              {pending === "design" && (
                                <div className={cn("flex w-full max-w-[320px] items-center gap-2 rounded-xl border border-dashed border-border px-3 py-2.5 text-[12px] text-muted-foreground", displayText && "mt-3")}>
                                  <RiLoader4Line size={14} className="animate-spin text-brand-500" />
                                  Designing…
                                </div>
                              )}
                              {/* Inline artifact cards */}
                              {itemArtifacts.length > 0 && (
                                <div className={`flex flex-col gap-2 ${displayText ? "mt-3" : ""}`}>
                                  {itemArtifacts.map((art) => {
                                    const shown = planState.plans.find((p) => p.id === art.id) ?? art;
                                    return (
                                      <ArtifactCard
                                        key={art.id}
                                        artifact={shown}
                                        planStatus={planState.statuses[art.id]}
                                        isActive={activeArtifactId === art.id}
                                        onClick={() => onArtifactOpen?.(shown)}
                                      />
                                    );
                                  })}
                                </div>
                              )}
                              {/* Inline file attachment cards */}
                              {item.fileAttachments && item.fileAttachments.length > 0 && (
                                <div className={`flex flex-col gap-2 ${displayText || itemArtifacts.length > 0 ? "mt-3" : ""}`}>
                                  {item.fileAttachments.map((file) => (
                                    <FileAttachmentCard key={file.file_id} file={file} />
                                  ))}
                                </div>
                              )}
                            </div>
                            {/* Hover actions: copy the reply's markdown, and how long it took */}
                            {(item.content || typeof item.responseTimeSeconds === "number") && !(isStreaming && i === items.length - 1) && (
                              <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover/reply:opacity-100 group-focus-within/reply:opacity-100 pointer-coarse:opacity-100">
                                {typeof item.responseTimeSeconds === "number" && (
                                  <span className="text-[12px] text-muted-foreground/70">
                                    {formatResponseTime(item.responseTimeSeconds)}
                                  </span>
                                )}
                                {item.content && (
                                  <CopyButton
                                    text={item.content}
                                    label="Copy reply"
                                    className="touch-target h-6 w-6 text-muted-foreground hover:text-foreground"
                                  />
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                      {showWorking && (
                        <WorkingIndicator
                          elapsedSeconds={streamElapsedSeconds}
                          status={latestStatus}
                          detail={accountSummary}
                        />
                      )}
                    </div>
                  </div>
                );
              })}

              {/* Recovery polling indicator */}
              {isRecovering && (
                <div className="flex justify-start animate-message-in mt-3 ml-8">
                  <span className="flex items-center gap-2 text-[14px] text-muted-foreground">
                    <RiLoader4Line size={14} className="animate-spin text-brand-500" />
                    {items.some((item) => item.deployQueued)
                      ? "Waiting for Loma to finish updating..."
                      : "Still working on your request..."}
                  </span>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Input — the bottom nav below it owns the home-indicator safe area */}
          <div className="sticky bottom-0 bg-background px-3 pt-2.5 pb-2.5 max-md:px-2 max-md:pt-1.5 max-md:pb-1.5 shrink-0">
            {readOnly ? <ReadOnlyComposerNotice /> : <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
              className="max-w-3xl mx-auto"
            >
              {isStreaming && <PetRunway running={!items.some((item) => item.role === "clarify" && !item.submitted)} />}
              <PendingFilesStrip files={pendingFiles} onRemove={removeFile} onExpandImage={setExpandedImage} />
              {voice.active
                ? <VoicePanel voice={voice} className="mb-1.5 rounded-2xl border border-border bg-card" />
                : voice.error && <p className="mb-1 text-xs text-destructive">{voice.error}</p>}

              <div className="relative">
              {activeCommand && !visibleSlashMatches.length && <SlashCommandHint template={activeCommand} />}
              <SlashCommandMenu
                matches={visibleSlashMatches}
                activeIndex={Math.min(slashIndex, Math.max(visibleSlashMatches.length - 1, 0))}
                onSelect={applyCommand}
                onHover={setSlashIndex}
              />
              {planBanner}
              <div className={cn(
                "flex bg-card border border-border composer-shadow focus-within:border-input transition-colors",
                isMobile ? "items-end gap-0.5 rounded-[26px] p-1" : "flex-col rounded-2xl",
                planMode && "border-brand-400 focus-within:border-brand-500",
              )}>
                {isMobile && conversationPlus}
                <Textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => updateComposerInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  onFocus={() => {
                    // Phones: the keyboard shrinks --app-h; keep the latest
                    // message in view above the composer.
                    if (standalone || isMobile) scrollToBottom();
                  }}
                  placeholder={isStreaming ? (queuedCount > 0 ? `${queuedCount} message${queuedCount > 1 ? "s" : ""} queued — type another or wait for agent` : (isMobile ? "Type a follow-up..." : "Type a follow-up while agent is working...")) : planMode ? planPlaceholder : "Ask anything, or type / for commands"}
                  rows={1}
                  className={cn(
                    "bg-transparent text-base md:text-[16px] text-foreground placeholder-muted-foreground focus:outline-none resize-none overflow-hidden border-0 focus-visible:ring-0 focus-visible:border-transparent rounded-none min-h-0",
                    isMobile ? "min-w-0 flex-1 self-center px-1 py-2.5 dark:bg-transparent" : "w-full px-3 pt-3 pb-1.5",
                  )}
                  style={{ maxHeight: "160px" }}
                />
                <div className={cn(isMobile ? "contents" : "flex flex-wrap items-center gap-x-1 gap-y-1.5 px-2 pb-2")}>
                  {!isMobile && <div className="contents">
                    {composerPickers}
                  </div>}
                  <div className="ml-auto flex items-center gap-1 max-md:gap-0 shrink-0">
                    {/* Dictation fills the box; voice mode is a live conversation. Voice replaces the mic while on. */}
                    {!voice.active && <DictationButton
                      onText={(t) => setInput((prev) => appendDictation(prev, t))}
                      mobileProminent
                      hideIdleOnMobile={!!input.trim() || pendingFiles.length > 0}
                      compactMobile
                    />}
                    {!voice.active && voiceAvailable && (
                      <VoiceModeButton voice={voice} className={cn((!!input.trim() || pendingFiles.length > 0) && "max-md:hidden")} />
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => fileInputRef.current?.click()}
                      title="Attach files"
                      className="text-muted-foreground hover:text-foreground max-md:hidden"
                    >
                      <RiAttachmentLine size={16} />
                    </Button>
                    {isStreaming && (
                      <Button
                        type="button"
                        variant="destructive"
                        size="icon-sm"
                        onClick={handleStop}
                        className="bg-red-500 hover:bg-red-600 text-white rounded-lg press-scale"
                        title="Stop agent (Esc)"
                      >
                        <RiStopLine size={16} />
                      </Button>
                    )}
                    <Button
                      type="submit"
                      size="icon-sm"
                      disabled={!input.trim() && pendingFiles.length === 0}
                      className={cn(
                        "bg-primary text-primary-foreground hover:bg-accent-200 hover:text-accent-on disabled:opacity-40 disabled:hover:bg-primary disabled:hover:text-primary-foreground rounded-lg press-scale max-md:size-11 max-md:rounded-full",
                        !input.trim() && pendingFiles.length === 0 && "max-md:hidden",
                      )}
                    >
                      <span className="sr-only">{planMode ? "Make a plan" : "Send message"}</span>
                      <RiSendPlaneLine size={16} />
                    </Button>
                  </div>
                </div>
              </div>
              </div>
            </form>}
          </div>
        </>
      )}
    </div>
  );
}

/** Pure function: apply a single event to the items list */
/** The open thought ends once Loma acts or answers: stamp how long it took. */
function closeThinking(items: ChatItem[]): ChatItem[] {
  const i = items.findLastIndex((item) => item.role === "thinking");
  const item = items[i];
  if (!item || item.thinkingSeconds !== undefined || item.thinkingStartedAt === undefined) return items;
  const updated = [...items];
  updated[i] = { ...item, thinkingSeconds: Math.max(1, Math.round((Date.now() - item.thinkingStartedAt) / 1000)) };
  return updated;
}

function applyEvent(items: ChatItem[], event: ChatEvent): ChatItem[] {
  // Always a copy: the cases below push/splice, and React may run this updater twice.
  let updated = [...(event.type === "text" || event.type === "tool_call" || event.type === "clarify"
    ? closeThinking(items)
    : items)];

  switch (event.type) {
    case "thinking": {
      updated = removeTransientStatusItems(updated);
      const last = updated[updated.length - 1];
      if (event.append && last?.role === "thinking" && last.thinkingSeconds === undefined) {
        updated[updated.length - 1] = { ...last, content: last.content + event.text };
      } else {
        updated = [...closeThinking(updated)];
        updated.push({ role: "thinking", content: event.text, thinkingStartedAt: Date.now() });
      }
      break;
    }

    case "status": {
      const message = event.elapsed_seconds
        ? `${event.message} (${event.elapsed_seconds}s)`
        : event.message;
      for (let i = updated.length - 1; i >= 0; i--) {
        if (updated[i].role === "status") {
          updated[i] = { ...updated[i], content: message, elapsedSeconds: event.elapsed_seconds };
          return updated;
        }
        if (updated[i].role === "assistant" || updated[i].role === "user") {
          break;
        }
      }
      updated.push({ role: "status", content: message, elapsedSeconds: event.elapsed_seconds });
      break;
    }

    case "turn":
      break;

    case "tool_call": {
      // A status line between two calls would otherwise split the group.
      updated = removeTransientStatusItems(updated);
      const step: Step = {
        type: "tool_call",
        name: event.name,
        tool_use_id: event.tool_use_id,
        status: "running",
        input: event.input,
      };
      const last = updated[updated.length - 1];
      if (last?.role === "steps") {
        updated[updated.length - 1] = {
          ...last,
          steps: [...(last.steps || []), step],
        };
      } else {
        updated.push({ role: "steps", content: "", steps: [step] });
      }
      break;
    }

    case "tool_result": {
      for (let i = updated.length - 1; i >= 0; i--) {
        if (updated[i].role === "steps" && updated[i].steps) {
          const steps = updated[i].steps!.map((s) =>
            s.tool_use_id === event.tool_use_id
              ? { ...s, status: (event.is_error ? "error" : "done") as Step["status"] }
              : s
          );
          updated[i] = { ...updated[i], steps };
          break;
        }
      }
      break;
    }

    case "clarify": {
      updated.push({
        role: "clarify",
        content: "",
        questions: event.questions,
      });
      break;
    }

    case "text": {
      updated = removeTransientStatusItems(updated);

      // Find the last assistant message, skipping queued user messages and steps
      // so that a user message sent mid-stream doesn't split the response.
      let assistantIdx = -1;
      for (let i = updated.length - 1; i >= 0; i--) {
        if (updated[i].role === "assistant") {
          assistantIdx = i;
          break;
        }
        if (updated[i].role === "user" && !updated[i].queued) break;
      }

      const target = assistantIdx >= 0 ? updated[assistantIdx] : null;
      const newText = target && event.append
        ? target.content + event.text
        : target
        ? (target.content ? target.content + "\n\n" + event.text : event.text)
        : event.text;

      const clarify = extractClarifyBlock(newText);
      if (clarify) {
        if (target) {
          if (clarify.before) {
            updated[assistantIdx] = { ...target, content: clarify.before };
          } else {
            updated.splice(assistantIdx, 1);
          }
        }
        updated.push({
          role: "clarify",
          content: clarify.before,
          questions: clarify.questions,
        });
        if (clarify.after) {
          updated.push({ role: "assistant", content: clarify.after });
        }
      } else {
        if (target) {
          updated[assistantIdx] = { ...target, content: newText };
        } else {
          updated.push({ role: "assistant", content: event.text });
        }
      }
      break;
    }
  }

  return updated;
}

/** Mark all remaining "running" steps as "done" */
function finalizeSteps(items: ChatItem[]): ChatItem[] {
  return items.map((item) => {
    if (item.role === "steps" && item.steps) {
      const hasRunning = item.steps.some((s) => s.status === "running");
      if (hasRunning) {
        return {
          ...item,
          steps: item.steps.map((s) =>
            s.status === "running" ? { ...s, status: "done" as const } : s
          ),
        };
      }
    }
    return item;
  });
}

/** Renders interactive clarifying questions with selectable option chips */
function ClarifyingQuestions({
  questions,
  submitted,
  selectedLabels: savedLabels,
  onSubmit,
}: {
  questions: ClarifyQuestion[];
  submitted: boolean;
  selectedLabels?: string[];
  onSubmit: (selected: string[], answer: string) => void;
}) {
  const [selections, setSelections] = useState<Record<number, Set<string>>>({});
  const [otherTexts, setOtherTexts] = useState<Record<number, string>>({});

  const toggleOption = (qIndex: number, label: string, multiSelect: boolean) => {
    if (submitted) return;
    setSelections((prev) => {
      const current = prev[qIndex] || new Set<string>();
      const next = new Set(current);
      if (multiSelect) {
        if (next.has(label)) next.delete(label);
        else next.add(label);
      } else {
        if (next.has(label)) {
          next.clear();
        } else {
          next.clear();
          next.add(label);
        }
      }
      return { ...prev, [qIndex]: next };
    });
  };

  const setOtherText = (qIndex: number, text: string) => {
    if (submitted) return;
    setOtherTexts((prev) => ({ ...prev, [qIndex]: text }));
  };

  const handleSubmit = () => {
    const allSelected: string[] = [];
    const answers: { question: string; answer: string }[] = [];
    questions.forEach((q, i) => {
      const picked = Array.from(selections[i] || []);
      allSelected.push(...picked);
      const other = otherTexts[i]?.trim();
      const parts = other ? [...picked, other] : picked;
      if (parts.length) answers.push({ question: q.question, answer: parts.join(", ") });
    });
    // One line per question, so the agent can tell which answer is which.
    const answer = questions.length === 1
      ? answers[0]?.answer ?? ""
      : answers.map((a) => `- ${a.question} ${a.answer}`).join("\n");
    onSubmit(allSelected, answer);
  };

  const hasAnySelection = Object.values(selections).some((s) => s.size > 0) ||
    Object.values(otherTexts).some((t) => t.trim().length > 0);

  return (
    <div className="space-y-4">
      {questions.map((q, qIndex) => {
        const selected = submitted
          ? new Set(savedLabels || [])
          : selections[qIndex] || new Set<string>();

        return (
          <div key={qIndex}>
            <p className="text-[15px] font-semibold text-foreground mb-2">{q.question}</p>
            <div className="flex flex-wrap gap-2 mb-2">
              {q.options.map((opt) => {
                const isSelected = selected.has(opt.label);
                return (
                  <Button
                    key={opt.label}
                    type="button"
                    variant="outline"
                    disabled={submitted}
                    onClick={() => toggleOption(qIndex, opt.label, q.multiSelect)}
                    className={cn(
                      "inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full text-[14px] font-medium border transition-all duration-150 h-auto",
                      submitted && !isSelected && "opacity-40",
                      isSelected
                        ? "bg-brand-50 border-brand-300 text-brand-700"
                        : "bg-muted border-border text-muted-foreground hover:bg-brand-50/50 hover:border-brand-200",
                      submitted ? "cursor-default" : "cursor-pointer"
                    )}
                    title={opt.description}
                  >
                    {isSelected && (
                      <RiCheckLine size={12} className="text-brand-600" />
                    )}
                    {opt.label}
                  </Button>
                );
              })}
            </div>
            {!submitted && q.options.some((o) => o.description) && (
              <div className="space-y-0.5 mb-2">
                {q.options.filter((o) => o.description).map((opt) => (
                  <p key={opt.label} className="text-[13px] text-muted-foreground">
                    <span className="font-medium text-foreground/70">{opt.label}</span> — {opt.description}
                  </p>
                ))}
              </div>
            )}
            {!submitted && (
              <div className="mt-2">
                <Input
                  type="text"
                  placeholder="Or type your own answer"
                  value={otherTexts[qIndex] || ""}
                  onChange={(e) => setOtherText(qIndex, e.target.value)}
                  className="h-10 text-[15px] md:text-[15px]"
                />
              </div>
            )}
          </div>
        );
      })}

      {submitted ? (
        <div className="flex items-center gap-1.5 text-[14px] text-green-700">
          <RiCheckLine size={16} />
          Answer sent
        </div>
      ) : (
        <Button
          type="button"
          disabled={!hasAnySelection}
          onClick={handleSubmit}
          className="bg-primary text-primary-foreground hover:bg-accent-200 hover:text-accent-on disabled:opacity-40 disabled:hover:bg-primary disabled:hover:text-primary-foreground h-10 px-4 text-[15px] font-medium rounded-xl press-scale"
          size="sm"
        >
          <RiSendPlaneLine size={16} />
          Send answer
        </Button>
      )}
    </div>
  );
}

function StatusPill({ message, elapsedSeconds }: { message: string; elapsedSeconds?: number }) {
  const suffix = typeof elapsedSeconds === "number" && elapsedSeconds > 0 ? ` · ${elapsedSeconds}s` : "";
  return (
    <span className="inline-flex items-center gap-1.5 text-[14px] text-muted-foreground">
      <RiLoader4Line size={14} className="animate-spin text-brand-500 flex-shrink-0" />
      <span className="truncate max-w-[560px]">{message}{suffix}</span>
    </span>
  );
}

/** The end of a run that didn't finish: what happened and one clear next step. */
function RunStoppedCard({ kind, detail, onContinue }: {
  kind: "interrupted" | "error";
  detail: string | null;
  onContinue: () => void;
}) {
  const failed = kind === "error";
  return (
    <div role="status" className="max-w-[560px] rounded-2xl border border-border bg-card p-4 animate-message-in">
      <div className="flex items-center gap-2.5">
        <span aria-hidden className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          failed ? "bg-destructive/10 text-destructive" : "bg-amber-500/15 text-amber-700 dark:text-amber-300")}>
          {failed ? <RiCloseLine size={16} /> : <RiPauseLine size={16} />}
        </span>
        <span className="text-[16px] font-semibold">{failed ? "Loma hit an error" : "Loma stopped before finishing"}</span>
      </div>
      <p className="mt-2 text-[15px] leading-[1.6] text-muted-foreground">
        {failed ? detail || "The run failed." : "The server restarted while Loma was working. It can pick up where it left off."}
      </p>
      <Button type="button" size="sm" className="mt-3 h-9 px-4 text-[14px]" onClick={onContinue}>
        {failed ? "Try again" : "Continue"}
      </Button>
    </div>
  );
}

function StepIcon({ status }: { status: Step["status"] }) {
  if (status === "running") return <RiLoader4Line size={14} className="animate-spin text-brand-500 flex-shrink-0" />;
  if (status === "error") return <RiCloseLine size={14} className="text-red-500 flex-shrink-0" />;
  return <RiCheckLine size={14} className="text-emerald-600/80 flex-shrink-0" />;
}

/** One step in plain words: what it did first, the tool after it. */
function StepLine({ step }: { step: Step }) {
  const tool = formatToolName(step.name || "tool");
  const summary = step.input ? summarizeToolInput(step.name || "", step.input) : "";
  return (
    <>
      <StepIcon status={step.status} />
      <span className={cn("min-w-0 truncate", step.status === "error" ? "text-red-600" : "text-foreground/80")}>
        {summary || tool}
      </span>
      {summary && <span className="shrink-0 font-mono text-[12px] text-muted-foreground/70">{tool}</span>}
    </>
  );
}

/** The model's thinking: streams in while it thinks, then folds to one line. */
function ThinkingBlock({ text, seconds, active }: { text: string; seconds?: number; active: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const previewRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (active && previewRef.current) previewRef.current.scrollTop = previewRef.current.scrollHeight;
  }, [active, text]);
  const open = active || expanded;
  const label = active ? "Thinking…" : seconds ? `Thought for ${seconds}s` : "Thought process";

  return (
    <div className="animate-message-in" data-chat-thinking="">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={open}
        className={cn(
          "flex max-w-full items-center gap-1.5 text-[14px] transition-colors hover:text-foreground",
          active ? "font-medium text-foreground" : "text-muted-foreground",
        )}
      >
        <RiLightbulbLine size={15} className={cn("shrink-0", active && "animate-pulse text-brand-500")} />
        <span>{label}</span>
        <RiArrowDownSLine size={14} className={cn("shrink-0 transition-transform", !open && "-rotate-90")} />
      </button>
      {open && (
        <div
          ref={previewRef}
          className={cn(
            "ml-2 mt-1.5 border-l border-border pl-4 text-muted-foreground [&_*]:text-inherit",
            active && !expanded && "max-h-24 overflow-hidden [mask-image:linear-gradient(to_bottom,transparent,black_35%)]",
          )}
        >
          <MarkdownContent content={text} className="text-[14px]" />
        </div>
      )}
    </div>
  );
}

type ProcessEntry =
  | { kind: "thought"; key: string; item: ChatItem; index: number }
  | { kind: "step"; key: string; step: Step };

/** A reply's thoughts and steps in one card, open while Loma works and folded
 * to one line after, so the reply text reads on its own. */
function ProcessGroup({ items, activeThought, working }: {
  items: ChatItem[];
  /** Index in `items` of the thought still streaming, or -1. */
  activeThought: number;
  working: boolean;
}) {
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const [showAll, setShowAll] = useState(false);

  const only = items.length === 1 ? items[0] : undefined;
  if (only?.role === "thinking") {
    return <ThinkingBlock text={only.content} seconds={only.thinkingSeconds} active={activeThought === 0} />;
  }
  if (only?.role === "steps" && only.steps?.length === 1) {
    return (
      <div className="flex min-w-0 max-w-full items-center gap-2 text-[14px] animate-message-in">
        <StepLine step={only.steps[0]} />
      </div>
    );
  }

  const counts = processCounts(items);
  const entries = items.flatMap((item, index): ProcessEntry[] => item.role === "thinking"
    ? [{ kind: "thought", key: `t${index}`, item, index }]
    : (item.steps || []).map((step, s) => ({ kind: "step", key: `s${index}-${s}`, step })));
  const open = expanded ?? working;
  // While working, the latest few entries; the rest are one click away.
  const hidden = working && !showAll ? Math.max(0, entries.length - LIVE_PROCESS_ENTRIES) : 0;
  const running = entries.findLast((entry) => entry.kind === "step" && entry.step.status === "running");
  const current = activeThought >= 0 ? "Thinking…"
    : running?.kind === "step" ? (running.step.input ? summarizeToolInput(running.step.name || "", running.step.input) : "") || formatToolName(running.step.name || "tool")
    : "";

  return (
    <div data-chat-process="" className="max-w-[640px] rounded-xl border border-border bg-card animate-message-in">
      <button
        type="button"
        onClick={() => setExpanded(!open)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-[14px] text-muted-foreground transition-colors hover:text-foreground"
      >
        {working ? <RiLoader4Line size={14} className="shrink-0 animate-spin text-brand-500" />
          : <RiCheckLine size={14} className="shrink-0 text-emerald-600/80" />}
        <span className={cn("shrink-0", working && "font-medium text-foreground")}>
          {working ? "Working" : processLabel({ ...counts, failed: 0 })}
        </span>
        {counts.failed > 0 && <span className="shrink-0 text-red-600">· {counts.failed} failed</span>}
        {working && counts.steps > 0 && <span className="shrink-0">· {stepsWord(counts.steps)}</span>}
        {working && !open && current && <span className="min-w-0 truncate text-foreground/70">· {current}</span>}
        <RiArrowDownSLine size={14} className={cn("ml-auto shrink-0 transition-transform", !open && "-rotate-90")} />
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-border px-3 py-2.5">
          {hidden > 0 && (
            <button type="button" onClick={() => setShowAll(true)} className="text-[13px] text-muted-foreground hover:text-foreground">
              Show {hidden} earlier
            </button>
          )}
          {entries.slice(hidden).map((entry) => entry.kind === "thought" ? (
            <ThinkingBlock
              key={entry.key}
              text={entry.item.content}
              seconds={entry.item.thinkingSeconds}
              active={entry.index === activeThought}
            />
          ) : (
            <div key={entry.key} className="flex min-w-0 items-center gap-2 text-[14px]">
              <StepLine step={entry.step} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
