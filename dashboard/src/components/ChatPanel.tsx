"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useSession } from "next-auth/react";
import { useStandalone } from "@/hooks/useStandalone";
import { useAgentModels } from "@/hooks/useAgentModels";
import { filesToChatFiles } from "@/lib/chatFiles";
import { ModelPicker } from "./composer/ModelPicker";
import { PendingFilesStrip } from "./composer/PendingFilesStrip";
import { DictationButton, appendDictation } from "./composer/DictationButton";
import { streamChat, fetchConversation, injectMessage, interruptAgent, basePath } from "../lib/api";
import type { ChatEvent, ChatFile, ChatMessage, ClarifyQuestion, Turn, PersistedArtifact } from "../lib/api";
import MarkdownContent from "./MarkdownContent";
import ArtifactCard from "./ArtifactCard";
import type { Artifact } from "./ArtifactViewer";
import CrosscutIcon from "./CrosscutIcon";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  RiCloseLine,
  RiArrowDownSLine,
  RiSendPlaneLine,
  RiLoader4Line,
  RiAttachmentLine,
  RiCheckLine,
  RiUploadLine,
  RiDownloadLine,
  RiStopLine,
  RiTimeLine,
  RiEditLine,
  RiDeleteBinLine,
} from "@remixicon/react";

const RECOVERY_MESSAGE = "Connection lost — checking on your request...";

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

export interface ChatItem {
  role: "user" | "assistant" | "steps" | "clarify" | "status";
  content: string;
  steps?: Step[];
  fileNames?: string[];
  files?: ChatFile[];
  questions?: ClarifyQuestion[];
  submitted?: boolean;
  selectedLabels?: string[];
  /** Artifact IDs referenced by this message (for inline artifact cards) */
  artifactIds?: string[];
  /** File attachments delivered by the agent */
  fileAttachments?: FileAttachment[];
  /** Client-observed response duration for this assistant message */
  responseTimeSeconds?: number;
  elapsedSeconds?: number;
  /** True when this user message is queued to be sent after the current stream finishes */
  queued?: boolean;
}

/** Pretty-print a tool name for display */
function formatToolName(name: string): string {
  if (name.startsWith("mcp__")) {
    const parts = name.replace("mcp__", "").split("__");
    return parts.map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" ");
  }
  return name;
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

function formatResponseTime(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}

function WorkingIndicator({ elapsedSeconds }: { elapsedSeconds: number }) {
  const display = elapsedSeconds < 10
    ? `${elapsedSeconds.toFixed(1)}s`
    : elapsedSeconds < 60
      ? `${elapsedSeconds.toFixed(0)}s`
      : `${Math.floor(elapsedSeconds / 60)}m ${Math.floor(elapsedSeconds % 60).toString().padStart(2, "0")}s`;

  return (
    <div className="working-indicator flex items-center gap-2 py-1.5 text-[12px] text-muted-foreground/70">
      <span className="grid grid-cols-3 gap-[3px]">
        {Array.from({ length: 9 }, (_, i) => (
          <span
            key={i}
            className="working-grid-dot block w-[3px] h-[3px] rounded-full bg-current"
            style={{ animationDelay: `${(i % 3) * 0.15 + Math.floor(i / 3) * 0.1}s` }}
          />
        ))}
      </span>
      <span className="tabular-nums font-mono text-[12px]">
        {display}
      </span>
    </div>
  );
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
  messages: Array<{ role: string; content: string; timestamp?: string }> | undefined,
  prompt: string,
  finalResponse: string,
  turns: Turn[],
  persistedArtifacts?: PersistedArtifact[],
): { items: ChatItem[]; artifacts: Artifact[] } {
  // If no turns data, fall back to simple message reconstruction
  if (!turns?.length) {
    if (messages?.length) {
      return {
        items: messages.map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
        })),
        artifacts: [],
      };
    }
    const items: ChatItem[] = [{ role: "user", content: prompt }];
    if (finalResponse) {
      items.push({ role: "assistant", content: finalResponse });
    }
    return { items, artifacts: [] };
  }

  // Collect follow-up user messages (skip the first one — it's the initial prompt)
  const followUpUserMessages: Array<{ content: string; timestamp: string }> = [];
  if (messages && messages.length > 1) {
    const userMessages = messages.filter((m) => m.role === "user");
    for (let i = 1; i < userMessages.length; i++) {
      if (userMessages[i].timestamp) {
        followUpUserMessages.push({
          content: userMessages[i].content,
          timestamp: userMessages[i].timestamp!,
        });
      }
    }
  }

  // Track which follow-up messages have been inserted
  const insertedFollowUps = new Set<number>();

  // Start with the initial user message
  const items: ChatItem[] = [{ role: "user", content: prompt }];

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
          items.push({ role: "user", content: followUpUserMessages[i].content });
        }
        insertedFollowUps.add(i);
      }
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
      items.push({ role: "steps", content: "", steps });
    }

    const textBlocks = turn.text_blocks || [];
    const text = textBlocks.map((b) => b.text).join("\n\n").trim();
    if (text) {
      items.push({ role: "assistant", content: text });
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
      items.push({ role: "user", content: followUpUserMessages[i].content });
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

    // Attach artifact IDs to assistant messages by matching timestamps to turns
    const turnTimestamps = turns.map((t) => t.timestamp);
    for (const pa of persistedArtifacts) {
      let bestTurnIdx = -1;
      for (let i = turnTimestamps.length - 1; i >= 0; i--) {
        if (turnTimestamps[i] <= pa.timestamp) {
          bestTurnIdx = i;
          break;
        }
      }
      if (bestTurnIdx >= 0) {
        let assistantCount = 0;
        for (let i = 0; i < items.length; i++) {
          if (items[i].role === "assistant") {
            if (assistantCount === bestTurnIdx) {
              const existingIds = items[i].artifactIds || [];
              if (!existingIds.includes(pa.artifact_id)) {
                items[i] = { ...items[i], artifactIds: [...existingIds, pa.artifact_id] };
              }
              break;
            }
            assistantCount++;
          }
        }
      } else {
        // Fallback: attach to the last assistant message
        for (let i = items.length - 1; i >= 0; i--) {
          if (items[i].role === "assistant") {
            const existingIds = items[i].artifactIds || [];
            if (!existingIds.includes(pa.artifact_id)) {
              items[i] = { ...items[i], artifactIds: [...existingIds, pa.artifact_id] };
            }
            break;
          }
        }
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
      <div className="relative max-w-[90vw] max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <img
          src={src}
          alt="Expanded view"
          className="max-w-full max-h-[90vh] object-contain rounded-lg shadow-2xl"
        />
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          className="absolute -top-3 -right-3 bg-gray-800 hover:bg-gray-700 text-white rounded-full shadow-lg"
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

export default function ChatPanel({
  initialItems,
  initialArtifacts,
  conversationId: initialConversationId,
  initialPrompt,
  initialFiles,
  initialModel,
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
} = {}) {
  const { data: session } = useSession();
  const standalone = useStandalone();
  const [items, setItems] = useState<ChatItem[]>(initialItems || []);
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversationId);
  const [input, setInput] = useState(initialPrompt || "");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null);
  const [streamElapsedSeconds, setStreamElapsedSeconds] = useState(0);
  const [isRecovering, setIsRecovering] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<ChatFile[]>(initialFiles || []);
  const [isDragOver, setIsDragOver] = useState(false);
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
  /** Internal artifact store — synced to parent via callbacks */
  const [internalArtifacts, setInternalArtifacts] = useState<Artifact[]>(initialArtifacts || []);
  // Use external artifacts if they have entries, otherwise fall back to internal.
  // Note: `[] || x` evaluates to `[]` because empty arrays are truthy in JS.
  const allArtifacts = externalArtifacts && externalArtifacts.length > 0
    ? externalArtifacts
    : internalArtifacts;
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const queuedMessagesRef = useRef<{ text: string; files?: ChatFile[] }[]>([]);
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
    inputRef.current?.focus();
  }, []);

  // Restore an unsent draft (e.g. the task drawer was closed by mistake).
  // Server-provided prompts (staged board drafts) win over the local draft.
  useEffect(() => {
    if (!draftStorageKey || initialPrompt) return;
    try {
      const saved = window.localStorage.getItem(draftStorageKey);
      if (saved) setInput((prev) => prev || saved);
    } catch {
      // localStorage unavailable (private mode) — drafts just don't persist.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftStorageKey]);

  // Persist unsent composer text; sending (or clearing) removes the draft.
  useEffect(() => {
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
  }, [input, adjustTextareaHeight]);

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
    if (conversationId) {
      interruptAgent(conversationId).catch(() => {});
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    } else if (isRecovering) {
      setIsRecovering(false);
      setIsStreaming(false);
      setStreamStartedAt(null);
    }
  }, [isRecovering, conversationId]);

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
        const { items: rebuilt } = rebuildItemsFromConversation(
          data.conversation.messages,
          data.conversation.prompt,
          data.conversation.final_response,
          data.turns,
        );
        setItems((prev) => {
          const queued = prev.filter((item) => item.queued);
          return queued.length > 0 ? [...rebuilt, ...queued] : rebuilt;
        });
        scrollToBottom();

        if (data.conversation.status !== "running") {
          setIsRecovering(false);
          setIsStreaming(false);
          if (data.conversation.status === "interrupted") {
            setItems((prev) => [
              ...prev,
              { role: "assistant", content: "The server restarted while processing your request. You can send a follow-up to continue." },
            ]);
          } else if (data.conversation.status === "error" && !data.conversation.final_response) {
            setItems((prev) => [
              ...prev,
              { role: "assistant", content: `Error: ${data.conversation.error || "Unknown error"}` },
            ]);
          }
          requestAnimationFrame(() => inputRef.current?.focus());
          return;
        }
      } catch {
        // Ignore polling errors, will retry
      }
      if (!stopped) setTimeout(poll, 3000);
    };

    poll();
    return () => { stopped = true; };
  }, [isRecovering, conversationId, scrollToBottom]);

  // If page loaded with a running conversation (refresh), enter recovery mode
  useEffect(() => {
    if (initialStatus === "running" && initialConversationId) {
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
    { fromQueue, includePendingFiles }: { fromQueue?: boolean; includePendingFiles?: boolean } = {},
  ) => {
    const displayText = overrideMessage ?? input.trim();
    if (!displayText && pendingFiles.length === 0) return;

    if (isStreaming) {
      const isOverride = overrideMessage !== undefined;
      const filesToQueue = !isOverride && pendingFiles.length > 0 ? [...pendingFiles] : undefined;
      const fileNames = filesToQueue?.map((f) => f.name);
      const hasFiles = filesToQueue && filesToQueue.length > 0;

      // Show message immediately
      setItems((prev) => [
        ...prev,
        { role: "user", content: displayText, fileNames, files: filesToQueue, queued: !conversationId || hasFiles },
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
    const filesToSend = (!isOverride || includePendingFiles) && pendingFiles.length > 0
      ? [...pendingFiles]
      : undefined;
    const fileNames = filesToSend?.map((f) => f.name);
    const displayMessage = displayText || `[${fileNames?.join(", ")}]`;

    const history = buildHistory(items);
    const responseStartedAt = performance.now();

    // Generate conversation ID client-side for new conversations so the URL
    // is updated immediately (before the SSE stream establishes).
    let activeConversationId = conversationId;
    if (!activeConversationId) {
      activeConversationId = createConversationId();
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
      setItems((prev) => [...prev, { role: "user", content: displayMessage, fileNames, files: filesToSend }]);
      // User sent a message — always snap down and resume following the stream.
      isAtBottomRef.current = true;
      scrollToBottom({ force: true });
    }
    setIsStreaming(true);
    setStreamStartedAt(performance.now());

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
          {
            role: "assistant",
            content: "*Stopped by user.* You can provide additional context or corrections below.",
          },
        ]);
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
      const responseTimeSeconds = Math.max(0.1, (performance.now() - responseStartedAt) / 1000);
      setItems((prev) => {
        const finalized = removeTransientStatusItems(finalizeSteps(prev));
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
            if (combinedFiles.length) {
              setPendingFiles(combinedFiles);
            }
            requestAnimationFrame(() => handleSend(combinedText, { fromQueue: true }));
          }
        } else {
          queuedMessagesRef.current = [];
          setQueuedCount(0);
          setItems((prev) => prev.map((item) =>
            item.queued ? { ...item, queued: false } : item
          ));
          const combinedText = queued.map((q) => q.text).join("\n\n");
          const combinedFiles = queued.flatMap((q) => q.files || []);
          if (combinedFiles.length) {
            setPendingFiles(combinedFiles);
          }
          requestAnimationFrame(() => handleSend(combinedText, { fromQueue: true }));
        }
      } else if (!enteredRecovery) {
        requestAnimationFrame(() => {
          inputRef.current?.focus();
        });
      }
    }
  };

  const handleClarifySubmit = useCallback(
    (itemIndex: number, selectedLabels: string[], otherText: string) => {
      setItems((prev) =>
        prev.map((item, i) =>
          i === itemIndex ? { ...item, submitted: true, selectedLabels } : item
        )
      );

      const parts = [...selectedLabels];
      if (otherText.trim()) {
        parts.push(otherText.trim());
      }
      const answer = parts.join(", ");

      handleSend(answer);
    },
    [items, conversationId, session, isStreaming, selectedModel],
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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;

      const imageFiles: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file" && item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) {
            const ext = file.type.split("/")[1] || "png";
            const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
            const named = new File([file], `clipboard-${timestamp}.${ext}`, {
              type: file.type,
            });
            imageFiles.push(named);
          }
        }
      }

      if (imageFiles.length > 0) {
        e.preventDefault();
        addFiles(imageFiles);
      }
    },
    [addFiles],
  );

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      addFiles(e.dataTransfer.files);
    }
  };

  const isEmptyState = items.length === 0 && !isStreaming;

  return (
    <div
      className="flex flex-col h-full"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
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
        <div className="flex flex-col items-center justify-center h-full px-4 md:px-6 animate-fade-in-up">
          <div className="mb-8 text-center">
            <h2 className="text-xl md:text-3xl font-heading font-normal text-foreground tracking-tight">
              What do you need to get done?
            </h2>
          </div>

          <div className="w-full max-w-full md:max-w-[680px]">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
            >
              <PendingFilesStrip files={pendingFiles} onRemove={removeFile} onExpandImage={setExpandedImage} />

              <div className="relative flex flex-col bg-card border border-border rounded-2xl shadow-sm focus-within:border-gray-300 transition-colors">
                <Textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => updateComposerInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  onFocus={() => {
                    // Installed PWA: the keyboard shrinks --app-h; keep the
                    // latest message in view above the composer.
                    if (standalone) scrollToBottom();
                  }}
                  placeholder={isStreaming ? "Type your next message..." : "What do you need to get done?"}
                  rows={2}
                  className="w-full bg-transparent px-4 md:px-5 pt-4 md:pt-5 pb-3 text-[15px] text-foreground placeholder-muted-foreground focus:outline-none resize-none overflow-hidden leading-relaxed border-0 focus-visible:ring-0 focus-visible:border-transparent rounded-none min-h-0"
                  style={{ maxHeight: "200px" }}
                />
                <div className="flex items-center justify-between gap-2 px-3 pb-3">
                  <div className="flex min-w-0 items-center">
                    <ModelPicker models={agentModels} selectedModel={selectedModel} onSelect={selectModel} loadState={modelLoadState} disabled={isStreaming} />
                  </div>
                  <div className="flex items-center gap-1 max-md:gap-2 shrink-0">
                    <DictationButton
                      onText={(t) => setInput((prev) => appendDictation(prev, t))}
                      mobileProminent
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => fileInputRef.current?.click()}
                      title="Attach files"
                      className="text-muted-foreground hover:text-foreground max-md:size-11 max-md:rounded-xl"
                    >
                      <RiAttachmentLine size={16} />
                    </Button>
                    <Button
                      type="submit"
                      disabled={!input.trim() && pendingFiles.length === 0}
                      className={cn(
                        "bg-accent-200 hover:bg-accent-300 disabled:opacity-30 disabled:hover:bg-accent-200 text-accent-on rounded-lg press-scale max-md:size-12 max-md:rounded-xl",
                        !input.trim() && pendingFiles.length === 0 && "max-md:hidden",
                      )}
                      size="icon-sm"
                    >
                      <RiSendPlaneLine size={16} />
                    </Button>
                  </div>
                </div>
              </div>
            </form>
          </div>
        </div>
      ) : (
        /* Normal chat layout */
        <>
          {/* Account info banner */}
          {accountInfo && (
            <div className="px-3 md:px-6 pt-5">
              <div className="max-w-3xl mx-auto">
                <div className="inline-flex items-center gap-2 text-[11px] text-muted-foreground/70">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                  <span>
                    {accountInfo.runtime === "opencode" ? (
                      <>
                        Using <strong>{accountInfo.provider}/{accountInfo.model}</strong> via OpenCode
                        {typeof accountInfo.pool_available === "number" && typeof accountInfo.pool_size === "number" ? (
                          <>
                            {" "}&middot;{" "}
                            {accountInfo.warm_session_used ? "warm session checked out" : "cold session"}
                            {" "}&middot; {accountInfo.pool_available}/{accountInfo.pool_size} warm
                            {accountInfo.pool_warming ? ` · ${accountInfo.pool_warming} warming` : ""}
                          </>
                        ) : null}
                      </>
                    ) : (
                      <>
                        Using{" "}
                        <strong>
                          {accountInfo.model ? `${accountInfo.model} via ` : ""}
                          {accountInfo.account_email || "unknown"}
                        </strong>
                        &apos;s {accountInfo.runtime === "codex" ? "ChatGPT" : "Claude"} subscription for this task
                        {typeof accountInfo.pool_available === "number" && typeof accountInfo.pool_size === "number"
                          ? <> &middot; {accountInfo.pool_available}/{accountInfo.pool_size} available</>
                          : null}
                      </>
                    )}
                  </span>
                </div>
              </div>
            </div>
          )}
          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-3 py-4" onScroll={handleMessagesScroll}>
            <div className="space-y-3 max-w-3xl mx-auto">
              {items.map((item, i) => {
                if (item.role === "steps") {
                  return <StepsGroup key={i} steps={item.steps || []} />;
                }

                if (item.role === "status") {
                  return <StatusLine key={i} message={item.content} elapsedSeconds={item.elapsedSeconds} />;
                }

                if (item.role === "clarify") {
                  return (
                    <div key={i} className="flex justify-start items-start animate-message-in gap-2">
                      <CrosscutIcon size={16} className="shrink-0 mt-px" />
                      <div className="chat-text min-w-0 flex-1 text-[13px] leading-relaxed break-words">
                        {item.content && (
                          <div className="mb-3 [&>*:first-child]:mt-0">
                            <MarkdownContent content={item.content} />
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
                    </div>
                  );
                }

                if (item.role === "user") {
                  return (
                    <div key={i} className="flex justify-end animate-message-in group/msg">
                      {item.queued && editingQueuedIndex !== i && (
                        <div className="flex items-center gap-0.5 mr-1.5 opacity-0 group-hover/msg:opacity-100 transition-opacity">
                          <button
                            type="button"
                            onClick={() => handleStartEditQueued(i)}
                            className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                            title="Edit queued message"
                          >
                            <RiEditLine size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteQueued(i)}
                            className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
                            title="Delete queued message"
                          >
                            <RiDeleteBinLine size={14} />
                          </button>
                        </div>
                      )}
                      <div className={cn(
                        "chat-text rounded-xl px-3 py-2 max-w-[75%] text-[13px] leading-relaxed break-words whitespace-pre-wrap",
                        item.queued ? "bg-muted/60 border border-dashed border-border" : "bg-muted"
                      )}>
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
                              className="w-full bg-transparent border-none outline-none resize-none text-[13px] leading-relaxed min-h-[2em]"
                              autoFocus
                            />
                            <div className="flex items-center justify-end gap-1.5">
                              <button
                                type="button"
                                onClick={handleCancelEditQueued}
                                className="text-[11px] text-muted-foreground hover:text-foreground px-2 py-0.5 rounded transition-colors"
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={() => handleSaveEditQueued(i)}
                                className="text-[11px] text-primary hover:text-primary/80 px-2 py-0.5 rounded transition-colors font-medium"
                              >
                                Save
                              </button>
                            </div>
                          </div>
                        ) : (
                          item.content || <TypingIndicator />
                        )}
                        {item.queued && editingQueuedIndex !== i && (
                          <div className="flex items-center gap-1 mt-1.5 text-[11px] text-muted-foreground">
                            <RiTimeLine size={12} />
                            <span>Queued — will send when agent finishes</span>
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

                // Assistant message — editorial style, no bubble
                return (
                  <div key={i} className="flex justify-start items-start animate-message-in gap-2">
                    <CrosscutIcon size={16} className="shrink-0 mt-px" />
                    <div className="chat-text min-w-0 flex-1 text-[13px] leading-relaxed break-words [&>*:first-child]:mt-0">
                      {item.content ? (
                        <MarkdownContent content={item.content} />
                      ) : (item.artifactIds?.length || item.fileAttachments?.length) ? (
                        null /* Artifact/file-only message — cards rendered below */
                      ) : (
                        <TypingIndicator />
                      )}
                      {/* Inline artifact cards */}
                      {item.artifactIds && item.artifactIds.length > 0 && (
                        <div className={`flex flex-col gap-2 ${item.content ? "mt-3" : ""}`}>
                          {item.artifactIds.map((artId) => {
                            const art = allArtifacts.find((a) => a.id === artId);
                            if (!art) return null;
                            return (
                              <ArtifactCard
                                key={artId}
                                artifact={art}
                                isActive={activeArtifactId === artId}
                                onClick={() => onArtifactOpen?.(art)}
                              />
                            );
                          })}
                        </div>
                      )}
                      {/* Inline file attachment cards */}
                      {item.fileAttachments && item.fileAttachments.length > 0 && (
                        <div className={`flex flex-col gap-2 ${item.content || (item.artifactIds && item.artifactIds.length > 0) ? "mt-3" : ""}`}>
                          {item.fileAttachments.map((file) => (
                            <FileAttachmentCard key={file.file_id} file={file} />
                          ))}
                        </div>
                      )}
                    </div>
                    {typeof item.responseTimeSeconds === "number" && (
                      <span className="shrink-0 text-[10px] text-muted-foreground/60 mt-0.5">{formatResponseTime(item.responseTimeSeconds)}</span>
                    )}
                  </div>
                );
              })}

              {/* Fallback indicator before any events arrive */}
              {isStreaming && !isRecovering && items.length > 0 && items[items.length - 1].role === "user" && (
                <div className="flex justify-start animate-message-in ml-6">
                  <StatusPill
                    message={
                      accountInfo?.runtime === "opencode"
                        ? "Waiting for OpenCode events..."
                        : "Waiting for agent events..."
                    }
                  />
                </div>
              )}

              {/* Recovery polling indicator */}
              {isRecovering && (
                <div className="flex justify-start animate-message-in">
                  <div className="text-[13px]">
                    <span className="flex items-center gap-2 text-muted-foreground">
                      <RiLoader4Line size={14} className="animate-spin text-brand-500" />
                      Still working on your request...
                    </span>
                  </div>
                </div>
              )}

              {isStreaming && (
                <div className="max-w-3xl mx-auto px-3">
                  <WorkingIndicator elapsedSeconds={streamElapsedSeconds} />
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Input — the bottom nav below it owns the home-indicator safe area */}
          <div className="sticky bottom-0 bg-background px-3 pt-2.5 pb-2.5 shrink-0">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
              className="max-w-3xl mx-auto"
            >
              <PendingFilesStrip files={pendingFiles} onRemove={removeFile} onExpandImage={setExpandedImage} />

              <div className="flex flex-col bg-muted border border-border rounded-2xl focus-within:border-gray-300 transition-colors">
                <Textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => updateComposerInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  onFocus={() => {
                    // Installed PWA: the keyboard shrinks --app-h; keep the
                    // latest message in view above the composer.
                    if (standalone) scrollToBottom();
                  }}
                  placeholder={isStreaming ? (queuedCount > 0 ? `${queuedCount} message${queuedCount > 1 ? "s" : ""} queued — type another or wait for agent` : "Type a follow-up while agent is working...") : "Ask the agent something..."}
                  rows={1}
                  className="w-full bg-transparent px-3 pt-3 pb-1.5 text-[13px] text-foreground placeholder-muted-foreground focus:outline-none resize-none overflow-hidden border-0 focus-visible:ring-0 focus-visible:border-transparent rounded-none min-h-0"
                  style={{ maxHeight: "160px" }}
                />
                <div className="flex items-center justify-between gap-2 px-2 pb-2">
                  <div className="flex min-w-0 items-center">
                    <ModelPicker models={agentModels} selectedModel={selectedModel} onSelect={selectModel} loadState={modelLoadState} disabled={isStreaming} />
                  </div>
                  <div className="flex items-center gap-1 max-md:gap-2 shrink-0">
                    <DictationButton
                      onText={(t) => setInput((prev) => appendDictation(prev, t))}
                      mobileProminent
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => fileInputRef.current?.click()}
                      title="Attach files"
                      className="text-muted-foreground hover:text-foreground max-md:size-11 max-md:rounded-xl"
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
                        "bg-accent-200 hover:bg-accent-300 disabled:opacity-40 disabled:hover:bg-accent-200 text-accent-on rounded-lg press-scale max-md:size-12 max-md:rounded-xl",
                        !input.trim() && pendingFiles.length === 0 && "max-md:hidden",
                      )}
                    >
                      <RiSendPlaneLine size={16} />
                    </Button>
                  </div>
                </div>
              </div>
            </form>
          </div>
        </>
      )}
    </div>
  );
}

/** Pure function: apply a single event to the items list */
function applyEvent(items: ChatItem[], event: ChatEvent): ChatItem[] {
  let updated = [...items];

  switch (event.type) {
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
  onSubmit: (selected: string[], otherText: string) => void;
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
    let allOther = "";
    for (let i = 0; i < questions.length; i++) {
      const sel = selections[i];
      if (sel) allSelected.push(...Array.from(sel));
      const other = otherTexts[i]?.trim();
      if (other) allOther = allOther ? `${allOther}; ${other}` : other;
    }
    onSubmit(allSelected, allOther);
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
            <p className="text-[13px] font-medium text-foreground mb-2">{q.question}</p>
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
                      "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-all duration-150 h-auto",
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
                  <p key={opt.label} className="text-xs text-muted-foreground">
                    <span className="font-medium text-foreground/70">{opt.label}</span> — {opt.description}
                  </p>
                ))}
              </div>
            )}
            {!submitted && (
              <div className="mt-2">
                <Input
                  type="text"
                  placeholder="Other — type your own..."
                  value={otherTexts[qIndex] || ""}
                  onChange={(e) => setOtherText(qIndex, e.target.value)}
                  className="h-7 text-xs"
                />
              </div>
            )}
          </div>
        );
      })}

      {submitted ? (
        <div className="flex items-center gap-1.5 text-xs text-green-600">
          <RiCheckLine size={14} />
          Selection submitted
        </div>
      ) : (
        <Button
          type="button"
          disabled={!hasAnySelection}
          onClick={handleSubmit}
          className="bg-accent-200 hover:bg-accent-300 disabled:opacity-40 disabled:hover:bg-accent-200 text-accent-on text-xs font-medium rounded-lg press-scale"
          size="sm"
        >
          <RiSendPlaneLine size={14} />
          Submit
        </Button>
      )}
    </div>
  );
}

function StatusPill({ message, elapsedSeconds }: { message: string; elapsedSeconds?: number }) {
  const suffix = typeof elapsedSeconds === "number" && elapsedSeconds > 0 ? ` · ${elapsedSeconds}s` : "";
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground">
      <RiLoader4Line size={12} className="animate-spin text-brand-500 flex-shrink-0" />
      <span className="truncate max-w-[560px]">{message}{suffix}</span>
    </span>
  );
}

function StatusLine({ message, elapsedSeconds }: { message: string; elapsedSeconds?: number }) {
  return (
    <div className="flex justify-start animate-message-in ml-6">
      <StatusPill message={message} elapsedSeconds={elapsedSeconds} />
    </div>
  );
}

function StepIcon({ status }: { status: Step["status"] }) {
  if (status === "running") return <RiLoader4Line size={10} className="animate-spin text-brand-500 flex-shrink-0" />;
  if (status === "error") return <RiCloseLine size={10} className="text-red-500 flex-shrink-0" />;
  return <RiCheckLine size={10} className="text-green-500/70 flex-shrink-0" />;
}

function StepsGroup({ steps }: { steps: Step[] }) {
  const [expanded, setExpanded] = useState(false);

  if (steps.length === 1) {
    const step = steps[0];
    const summary = step.input ? summarizeToolInput(step.name || "", step.input) : "";
    return (
      <div className="flex items-center gap-1.5 text-[12px] text-muted-foreground animate-message-in ml-6">
        <StepIcon status={step.status} />
        <span className={step.status === "error" ? "text-red-600" : ""}>{formatToolName(step.name || "tool")}</span>
        {summary && <span className="text-muted-foreground/40 truncate max-w-[300px]">· {summary}</span>}
      </div>
    );
  }

  const hasRunning = steps.some(s => s.status === "running");
  const errorCount = steps.filter(s => s.status === "error").length;
  const uniqueNames = [...new Set(steps.map(s => formatToolName(s.name || "tool")))];
  const preview = uniqueNames.slice(0, 4).join(", ") + (uniqueNames.length > 4 ? ", …" : "");

  return (
    <div className="animate-message-in ml-6">
      <button
        onClick={() => setExpanded(e => !e)}
        className="flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground/80 transition-colors"
      >
        <RiArrowDownSLine
          size={12}
          className={cn("transition-transform flex-shrink-0", !expanded && "-rotate-90")}
        />
        {hasRunning && <RiLoader4Line size={10} className="animate-spin text-brand-500 flex-shrink-0" />}
        <span>{steps.length} tool calls</span>
        {errorCount > 0 && <span className="text-red-500">· {errorCount} failed</span>}
        {!expanded && <span className="text-muted-foreground/40">· {preview}</span>}
      </button>
      {expanded && (
        <div className="ml-4 mt-0.5 space-y-px">
          {steps.map((step, i) => {
            const summary = step.input ? summarizeToolInput(step.name || "", step.input) : "";
            return (
              <div key={i} className="flex items-center gap-1.5 text-[11px] text-muted-foreground py-px">
                <StepIcon status={step.status} />
                <span className={step.status === "error" ? "text-red-600" : ""}>{formatToolName(step.name || "tool")}</span>
                {summary && <span className="text-muted-foreground/40 truncate max-w-[300px]">{summary}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
