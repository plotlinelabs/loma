"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useSession } from "next-auth/react";
import { streamChat, fetchConversation, fetchAgentModels, basePath } from "../lib/api";
import type { AgentModel, ChatEvent, ChatFile, ChatMessage, ClarifyQuestion, Turn, PersistedArtifact } from "../lib/api";
import MarkdownContent from "./MarkdownContent";
import ArtifactCard from "./ArtifactCard";
import type { Artifact } from "./ArtifactViewer";

const RECOVERY_MESSAGE = "Connection lost \u2014 checking on your request...";
const MODEL_STORAGE_KEY = "dashboard-chat-selected-model";
const FAVORITE_MODEL_IDS = [
  "opencode-go/deepseek-v4-flash",
  "anthropic/claude-opus-4-8",
  "anthropic/claude-opus-4-7",
  "anthropic/claude-opus-4-6",
  "openai/gpt-5.5",
] as const;

function favoriteModelRank(model: AgentModel): number | null {
  const index = FAVORITE_MODEL_IDS.indexOf(model.id as typeof FAVORITE_MODEL_IDS[number]);
  return index === -1 ? null : index;
}

function isFavoriteModel(model: AgentModel): boolean {
  return favoriteModelRank(model) !== null;
}

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const TEXT_EXTENSIONS = new Set([
  "txt", "csv", "json", "py", "md", "js", "ts", "tsx", "jsx", "yml", "yaml",
  "xml", "html", "css", "log", "sh", "sql", "env", "cfg", "ini", "toml",
]);
const BINARY_EXTENSIONS = new Set([
  "xlsx", "xlsm", "xls", "pdf", "docx", "pptx",
  "zip", "tar", "gz", "7z", "rar", "tgz",       // archives
]);
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB
const MAX_TEXT_SIZE = 50 * 1024; // 50 KB

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

/** Read a File into a ChatFile object */
async function readFileAsChatFile(file: File): Promise<ChatFile | null> {
  if (file.size > MAX_FILE_SIZE) return null;

  const ext = file.name.split(".").pop()?.toLowerCase() || "";
  const isImage = IMAGE_TYPES.has(file.type);
  const isText = TEXT_EXTENSIONS.has(ext) || file.type.startsWith("text/");
  const isBinary = BINARY_EXTENSIONS.has(ext);

  if (!isImage && !isText && !isBinary) {
    console.warn(`Unsupported file type: ${file.name} (${file.type})`);
    return null;
  }

  if (isImage) {
    const buffer = await file.arrayBuffer();
    const base64 = btoa(
      new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), "")
    );
    return { name: file.name, mimetype: file.type, type: "image", data: base64 };
  }

  if (isBinary) {
    const buffer = await file.arrayBuffer();
    const base64 = btoa(
      new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), "")
    );
    return { name: file.name, mimetype: file.type || "application/octet-stream", type: "binary", data: base64 };
  }

  // Text file
  let text = await file.text();
  if (text.length > MAX_TEXT_SIZE) {
    text = text.slice(0, MAX_TEXT_SIZE) + `\n\n... [truncated, file was ${(file.size / 1024).toFixed(0)} KB]`;
  }
  return { name: file.name, mimetype: file.type || "text/plain", type: "text", data: text };
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

  // Collect follow-up user messages (skip the first one \u2014 it's the initial prompt)
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
function PendingImageThumbnail({
  file,
  index,
  onRemove,
  onExpand,
}: {
  file: ChatFile;
  index: number;
  onRemove: (i: number) => void;
  onExpand: (src: string) => void;
}) {
  const src = `data:${file.mimetype};base64,${file.data}`;
  return (
    <span className="relative inline-flex group">
      <button
        type="button"
        onClick={() => onExpand(src)}
        className="w-12 h-12 rounded-lg overflow-hidden border border-gray-200 hover:border-gray-300 transition-colors flex-shrink-0"
      >
        <img src={src} alt={file.name} className="w-full h-full object-cover" />
      </button>
      <button
        type="button"
        onClick={() => onRemove(index)}
        className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-gray-600 hover:bg-gray-800 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
      >
        <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
        </svg>
      </button>
    </span>
  );
}

/** Pending file badge (non-image) */
function PendingFileBadge({
  file,
  index,
  onRemove,
}: {
  file: ChatFile;
  index: number;
  onRemove: (i: number) => void;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 bg-gray-100 text-gray-700 text-xs px-2.5 py-1 rounded-lg border border-gray-200">
      <svg className="w-3 h-3 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="m18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13" />
      </svg>
      {file.name}
      <button
        type="button"
        onClick={() => onRemove(index)}
        className="text-gray-400 hover:text-gray-600 ml-0.5"
      >
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
        </svg>
      </button>
    </span>
  );
}

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
      className="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4 animate-fade-in cursor-pointer"
      onClick={onClose}
    >
      <div className="relative max-w-[90vw] max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <img
          src={src}
          alt="Expanded view"
          className="max-w-full max-h-[90vh] object-contain rounded-lg shadow-2xl"
        />
        <button
          onClick={onClose}
          className="absolute -top-3 -right-3 w-8 h-8 bg-gray-800 hover:bg-gray-700 text-white rounded-full flex items-center justify-center shadow-lg transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
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
      className={`flex items-center gap-3 px-3 py-2.5 rounded-xl border border-gray-200 hover:border-gray-300 hover:shadow-sm transition-all group ${bg}`}
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
        <p className="text-sm font-medium text-gray-800 truncate group-hover:text-gray-900">
          {file.name}
        </p>
        <p className="text-xs text-gray-500">
          {formatFileSize(file.size)} · {file.mime_type.split("/")[1]?.toUpperCase() || "FILE"}
        </p>
      </div>
      <svg className={`w-4 h-4 ${color} opacity-60 group-hover:opacity-100 flex-shrink-0 transition-opacity`} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
      </svg>
    </a>
  );
}

export default function ChatPanel({
  initialItems,
  initialArtifacts,
  conversationId: initialConversationId,
  initialPrompt,
  initialStatus,
  activeArtifactId,
  onArtifactOpen,
  onArtifactClose,
  artifacts: externalArtifacts,
  onConversationCreated,
}: {
  initialItems?: ChatItem[];
  /** Artifacts restored from history (persisted in MongoDB) */
  initialArtifacts?: Artifact[];
  conversationId?: string;
  initialPrompt?: string;
  initialStatus?: string;
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
} = {}) {
  const { data: session } = useSession();
  const [items, setItems] = useState<ChatItem[]>(initialItems || []);
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversationId);
  const [input, setInput] = useState(initialPrompt || "");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null);
  const [streamElapsedSeconds, setStreamElapsedSeconds] = useState(0);
  const [isRecovering, setIsRecovering] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<ChatFile[]>([]);
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
  const [agentModels, setAgentModels] = useState<AgentModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [modelLoadState, setModelLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [modelSearch, setModelSearch] = useState("");
  /** Internal artifact store \u2014 synced to parent via callbacks */
  const [internalArtifacts, setInternalArtifacts] = useState<Artifact[]>(initialArtifacts || []);
  // Use external artifacts if they have entries, otherwise fall back to internal.
  // Note: `[] || x` evaluates to `[]` because empty arrays are truthy in JS.
  const allArtifacts = externalArtifacts && externalArtifacts.length > 0
    ? externalArtifacts
    : internalArtifacts;
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const selectedModelInfo = useMemo(
    () => agentModels.find((model) => model.id === selectedModel) || null,
    [agentModels, selectedModel],
  );

  const filteredAgentModels = useMemo(() => {
    const query = modelSearch.trim().toLowerCase();
    if (!query) return agentModels;
    return agentModels.filter((model) => {
      const haystack = `${model.label} ${model.id} ${model.provider_id} ${model.model_id}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [agentModels, modelSearch]);

  const recommendedAgentModels = useMemo(
    () => filteredAgentModels
      .filter(isFavoriteModel)
      .sort((a, b) => (favoriteModelRank(a) ?? 99) - (favoriteModelRank(b) ?? 99)),
    [filteredAgentModels],
  );

  const groupedAgentModels = useMemo(() => {
    const groups: Array<{ providerId: string; models: AgentModel[] }> = [];
    for (const model of filteredAgentModels.filter((item) => !isFavoriteModel(item))) {
      const group = groups.find((item) => item.providerId === model.provider_id);
      if (group) {
        group.models.push(model);
      } else {
        groups.push({ providerId: model.provider_id, models: [model] });
      }
    }
    return groups;
  }, [filteredAgentModels]);

  useEffect(() => {
    let cancelled = false;

    async function loadModels() {
      setModelLoadState("loading");
      try {
        const catalog = await fetchAgentModels();
        if (cancelled) return;
        const models = catalog.models || [];
        setAgentModels(models);

        const saved = typeof window !== "undefined"
          ? window.localStorage.getItem(MODEL_STORAGE_KEY)
          : null;
        const savedIsValid = saved && models.some((model) => model.id === saved);
        const nextModel = savedIsValid
          ? saved
          : catalog.default_model || models[0]?.id || "";
        setSelectedModel(nextModel);
        setModelLoadState("ready");
      } catch (e) {
        if (cancelled) return;
        console.warn("Failed to load agent models", e);
        setAgentModels([]);
        setSelectedModel("");
        setModelLoadState("error");
      }
    }

    loadModels();
    return () => {
      cancelled = true;
    };
  }, []);

  const scrollToBottom = useCallback(() => {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 50);
  }, []);

  useEffect(() => {
    if (initialItems?.length) {
      setItems(initialItems);
    }
  }, [initialItems]);

  useEffect(() => {
    if (initialConversationId) {
      setConversationId(initialConversationId);
    }
  }, [initialConversationId]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

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
      setStreamElapsedSeconds(Math.max(0, Math.floor((performance.now() - streamStartedAt) / 1000)));
    };
    tick();
    const interval = window.setInterval(tick, 1000);
    return () => window.clearInterval(interval);
  }, [isStreaming, streamStartedAt]);

  useEffect(() => {
    if (!modelPickerOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!modelPickerRef.current?.contains(event.target as Node)) {
        setModelPickerOpen(false);
      }
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setModelPickerOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [modelPickerOpen]);

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
    const files = Array.from(fileList);
    const chatFiles: ChatFile[] = [];
    const rejected: string[] = [];
    for (const f of files) {
      if (f.size > MAX_FILE_SIZE) {
        rejected.push(`${f.name} (too large \u2014 max ${MAX_FILE_SIZE / 1024 / 1024}MB)`);
        continue;
      }
      const cf = await readFileAsChatFile(f);
      if (cf) {
        chatFiles.push(cf);
      } else {
        rejected.push(f.name);
      }
    }
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
  }, []);

  useEffect(() => {
    const handleEscapeKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isStreaming) {
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

        // Rebuild items from turns on every poll so new steps appear
        const { items: rebuilt } = rebuildItemsFromConversation(
          data.conversation.messages,
          data.conversation.prompt,
          data.conversation.final_response,
          data.turns,
        );
        setItems(rebuilt);
        scrollToBottom();

        if (data.conversation.status !== "running") {
          setIsRecovering(false);
          setIsStreaming(false);
          if (data.conversation.status === "error" && !data.conversation.final_response) {
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

  const handleSend = async (overrideMessage?: string) => {
    const message = overrideMessage ?? input.trim();
    if ((!message && pendingFiles.length === 0) || isStreaming) return;

    const isOverride = overrideMessage !== undefined;
    const filesToSend = !isOverride && pendingFiles.length > 0 ? [...pendingFiles] : undefined;
    const fileNames = filesToSend?.map((f) => f.name);
    const displayMessage = message || `[${fileNames?.join(", ")}]`;

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
    }
    setItems((prev) => [...prev, { role: "user", content: displayMessage, fileNames, files: filesToSend }]);
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
          // Server confirmed the ID \u2014 keep URL in sync in case it differs
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
        // Stream broke but agent may still be running \u2014 enter recovery mode
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
      }
      abortControllerRef.current = null;
      scrollToBottom();
      if (!enteredRecovery) {
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

  const handleModelChange = (value: string) => {
    setSelectedModel(value);
    if (value) {
      window.localStorage.setItem(MODEL_STORAGE_KEY, value);
    } else {
      window.localStorage.removeItem(MODEL_STORAGE_KEY);
    }
    setModelPickerOpen(false);
    setModelSearch("");
  };

  const renderModelPicker = (compact = false) => {
    const disabled = isStreaming || modelLoadState !== "ready" || agentModels.length === 0;
    const title = modelLoadState === "error"
      ? "Model list unavailable; backend default will be used"
      : "Choose model for dashboard chat";
    const providerLabel = selectedModelInfo?.provider_id || "Backend";
    const modelLabel = selectedModelInfo
      ? selectedModelInfo.label.split("·").pop()?.trim() || selectedModelInfo.model_id
      : modelLoadState === "loading"
      ? "Loading models"
      : modelLoadState === "error"
      ? "Default model"
      : "Choose model";
    const isActiveComposer = compact;
    const providerDisplay = selectedModelInfo
      ? selectedModelInfo.provider_id === "anthropic"
        ? "Claude"
        : selectedModelInfo.provider_id === "opencode-go"
        ? "OpenCode Go"
        : selectedModelInfo.provider_id === "openai"
        ? "OpenAI"
        : selectedModelInfo.provider_id
      : providerLabel;

    return (
      <div
        ref={modelPickerRef}
        className={`relative min-w-0 ${isActiveComposer ? "max-w-full" : "max-w-[300px]"}`}
        title={title}
      >
        <button
          type="button"
          disabled={disabled}
          onClick={() => setModelPickerOpen((open) => !open)}
          className={
            isActiveComposer
              ? "group inline-flex h-8 max-w-full items-center gap-2 rounded-full border border-gray-200 bg-white px-2.5 text-left text-gray-600 shadow-[0_1px_0_rgba(10,10,10,0.03)] transition-colors hover:border-gray-300 hover:text-gray-900 focus:outline-none focus:ring-2 focus:ring-accent-200 disabled:cursor-not-allowed disabled:opacity-55 dark:border-gray-300 dark:bg-gray-100 dark:text-gray-700 dark:hover:bg-gray-200"
              : "group inline-flex h-10 max-w-full items-center gap-2 rounded-xl border border-gray-200 bg-white/90 px-2.5 text-left text-gray-700 shadow-[0_1px_0_rgba(10,10,10,0.04)] transition-all hover:border-gray-300 hover:bg-white focus:outline-none focus:ring-2 focus:ring-accent-200 disabled:cursor-not-allowed disabled:opacity-55 dark:border-gray-300 dark:bg-gray-100 dark:text-gray-800 dark:hover:bg-gray-200 dark:focus:ring-accent-300/40"
          }
        >
          {isActiveComposer ? (
            <>
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
              <span className="min-w-0 truncate text-xs font-medium">
                <span className="text-gray-400 dark:text-gray-500">{providerDisplay}</span>
                <span className="mx-1 text-gray-300">/</span>
                <span className="font-semibold text-gray-800 dark:text-gray-900">{modelLabel}</span>
              </span>
            </>
          ) : (
            <>
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-[#1F1D1A] text-[10px] font-semibold text-white dark:bg-accent-200 dark:text-accent-on">
                AI
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[11px] font-medium leading-3 text-gray-400 dark:text-gray-500">
                  {providerLabel}
                </span>
                <span className="block truncate text-[13px] font-semibold leading-4 text-gray-800 dark:text-gray-900">
                  {modelLabel}
                </span>
              </span>
            </>
          )}
          <svg
            className={`${isActiveComposer ? "h-3.5 w-3.5" : "h-4 w-4"} shrink-0 text-gray-400 transition-transform ${modelPickerOpen ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.8}
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m6 9 6 6 6-6" />
          </svg>
        </button>

        {modelPickerOpen && !disabled && (
          <div className="absolute bottom-full left-0 z-40 mb-2 w-[min(88vw,390px)] overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl shadow-gray-900/15 dark:border-gray-300 dark:bg-gray-100 dark:shadow-black/40">
            <div className="border-b border-gray-100 p-2.5 dark:border-gray-300">
              <div className="flex items-center gap-2 rounded-xl border border-gray-200 bg-gray-50 px-2.5 py-2 dark:border-gray-300 dark:bg-gray-50">
                <svg className="h-4 w-4 shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.4a6.75 6.75 0 1 1-13.5 0 6.75 6.75 0 0 1 13.5 0Z" />
                </svg>
                <input
                  value={modelSearch}
                  onChange={(event) => setModelSearch(event.target.value)}
                  placeholder="Search models"
                  className="min-w-0 flex-1 bg-transparent text-sm text-gray-800 placeholder-gray-400 outline-none dark:text-gray-900"
                  autoFocus
                />
              </div>
            </div>

            <div className="max-h-72 overflow-y-auto p-1.5">
              {recommendedAgentModels.length === 0 && groupedAgentModels.length === 0 ? (
                <div className="px-3 py-8 text-center text-sm text-gray-500">
                  No models match that search.
                </div>
              ) : (
                <>
                  {recommendedAgentModels.length > 0 && (
                    <div className="py-1">
                      <div className="px-2.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">
                        Favorites
                      </div>
                      {recommendedAgentModels.map((model) => {
                      const isSelected = model.id === selectedModel;
                      const itemModelLabel = model.label.split("·").pop()?.trim() || model.model_id;
                      return (
                        <button
                          key={model.id}
                          type="button"
                          onClick={() => handleModelChange(model.id)}
                          className={`flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition-colors ${
                            isSelected
                              ? "bg-[#1F1D1A] text-white dark:bg-accent-200 dark:text-accent-on"
                              : "text-gray-700 hover:bg-gray-50 dark:text-gray-800 dark:hover:bg-gray-200"
                          }`}
                        >
                          <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[10px] font-semibold ${
                            isSelected
                              ? "bg-white/15 text-white"
                              : "bg-gray-100 text-gray-500 dark:bg-gray-200 dark:text-gray-600"
                          }`}>
                            {model.provider_id.slice(0, 2).toUpperCase()}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold">
                              {itemModelLabel}
                            </span>
                            <span className={`block truncate text-xs ${isSelected ? "text-white/65 dark:text-[#5C5650]" : "text-gray-400"}`}>
                              {model.id}
                            </span>
                          </span>
                          <span className="flex shrink-0 items-center gap-1.5">
                            {model.supports_reasoning && (
                              <span className={`hidden rounded-md px-1.5 py-0.5 text-[10px] font-medium sm:inline-flex ${
                                isSelected ? "bg-white/15 text-white/80 dark:bg-[#1F1D1A]/10 dark:text-[#1F1D1A]/75" : "bg-accent-100 text-gray-700 dark:bg-accent-100 dark:text-gray-800"
                              }`}>
                                reasoning
                              </span>
                            )}
                            {isSelected && (
                              <svg className="h-4 w-4 text-accent-200 dark:text-[#1F1D1A]" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                              </svg>
                            )}
                          </span>
                        </button>
                      );
                      })}
                    </div>
                  )}
                  {groupedAgentModels.map((group) => (
                    <div key={group.providerId} className="py-1">
                      <div className="px-2.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">
                        {group.providerId}
                      </div>
                      {group.models.map((model) => {
                        const isSelected = model.id === selectedModel;
                        const itemModelLabel = model.label.split("·").pop()?.trim() || model.model_id;
                        return (
                          <button
                            key={model.id}
                            type="button"
                            onClick={() => handleModelChange(model.id)}
                            className={`flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left transition-colors ${
                              isSelected
                                ? "bg-[#1F1D1A] text-white dark:bg-accent-200 dark:text-accent-on"
                                : "text-gray-700 hover:bg-gray-50 dark:text-gray-800 dark:hover:bg-gray-200"
                            }`}
                          >
                            <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[10px] font-semibold ${
                              isSelected
                                ? "bg-white/15 text-white"
                                : "bg-gray-100 text-gray-500 dark:bg-gray-200 dark:text-gray-600"
                            }`}>
                              {model.provider_id.slice(0, 2).toUpperCase()}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-semibold">
                                {itemModelLabel}
                              </span>
                              <span className={`block truncate text-xs ${isSelected ? "text-white/65 dark:text-[#5C5650]" : "text-gray-400"}`}>
                                {model.id}
                              </span>
                            </span>
                            <span className="flex shrink-0 items-center gap-1.5">
                              {model.supports_reasoning && (
                                <span className={`hidden rounded-md px-1.5 py-0.5 text-[10px] font-medium sm:inline-flex ${
                                  isSelected ? "bg-white/15 text-white/80 dark:bg-[#1F1D1A]/10 dark:text-[#1F1D1A]/75" : "bg-accent-100 text-gray-700 dark:bg-accent-100 dark:text-gray-800"
                                }`}>
                                  reasoning
                                </span>
                              )}
                              {isSelected && (
                                <svg className="h-4 w-4 text-accent-200 dark:text-[#1F1D1A]" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                                </svg>
                              )}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    );
  };

  /** Render the pending files strip (shared between empty state and normal chat input) */
  const renderPendingFiles = () => {
    if (pendingFiles.length === 0) return null;
    return (
      <div className="flex flex-wrap items-end gap-1.5 mb-2">
        {pendingFiles.map((f, i) =>
          f.type === "image" ? (
            <PendingImageThumbnail
              key={i}
              file={f}
              index={i}
              onRemove={removeFile}
              onExpand={setExpandedImage}
            />
          ) : (
            <PendingFileBadge key={i} file={f} index={i} onRemove={removeFile} />
          )
        )}
      </div>
    );
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
          <div className="text-brand-600 font-medium text-sm flex items-center gap-2">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
            </svg>
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
            <h2 className="text-xl md:text-3xl font-semibold text-gray-900 tracking-tight">
              What do you need to get done?
            </h2>
            <p className="text-[15px] text-gray-400 mt-2 max-w-md mx-auto">
              Say the word. I&apos;ll do the rest. Go.
            </p>
          </div>

          <div className="w-full max-w-full md:max-w-[680px]">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
            >
              {renderPendingFiles()}

              <div className="relative flex flex-col bg-surface border border-gray-200 rounded-2xl shadow-sm focus-within:shadow-md focus-within:border-gray-300 transition-all">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder="What do you need to get done?"
                  disabled={isStreaming}
                  rows={2}
                  className="w-full bg-transparent px-4 md:px-5 pt-4 md:pt-5 pb-3 text-[15px] text-gray-900 placeholder-gray-400 focus:outline-none disabled:opacity-50 resize-none overflow-hidden leading-relaxed"
                  style={{ maxHeight: "200px" }}
                />
                <div className="flex items-center justify-between px-3 pb-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={isStreaming}
                      className="p-2 text-gray-400 hover:text-gray-600 disabled:opacity-40 transition-colors rounded-lg hover:bg-gray-50 flex-shrink-0"
                      title="Attach files"
                    >
                      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="m18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13" />
                      </svg>
                    </button>
                    {renderModelPicker(true)}
                  </div>
                  <button
                    type="submit"
                    disabled={isStreaming || (!input.trim() && pendingFiles.length === 0)}
                    className="bg-accent-200 hover:bg-accent-300 disabled:opacity-30 disabled:hover:bg-accent-200 text-accent-on p-2.5 rounded-xl transition-colors press-scale flex-shrink-0"
                  >
                    {isStreaming ? (
                      <svg className="animate-spin w-5 h-5" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                    ) : (
                      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>
              <p className="text-xs text-gray-400 mt-3 text-center hidden md:block">
                Press Enter to send, Shift+Enter for new line. Drag & drop, paste, or click the clip to attach files.
              </p>
            </form>
          </div>
        </div>
      ) : (
        /* Normal chat layout */
        <>
          {/* Account info banner */}
          {accountInfo && (
            <div className="px-3 md:px-6 pt-3">
              <div className="max-w-3xl mx-auto">
                <div className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-full bg-gray-50 text-gray-500 border border-gray-200">
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
                        &apos;s Claude subscription for this task
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
          <div className="flex-1 overflow-y-auto px-3 md:px-6 py-4">
            <div className="space-y-4 max-w-3xl mx-auto">
              {items.map((item, i) => {
                if (item.role === "steps") {
                  return <StepsGroup key={i} steps={item.steps || []} />;
                }

                if (item.role === "status") {
                  return <StatusLine key={i} message={item.content} elapsedSeconds={item.elapsedSeconds} />;
                }

                if (item.role === "clarify") {
                  return (
                    <div key={i} className="flex justify-start animate-message-in">
                      <div className="w-7 h-7 bg-brand-100 rounded-lg flex items-center justify-center flex-shrink-0 mr-2 md:mr-3 mt-0.5 hidden md:flex">
                        <span className="text-xs font-bold text-brand-700">G</span>
                      </div>
                      <div className="max-w-[90%] md:max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed bg-surface border border-gray-200 text-gray-800 rounded-bl-md shadow-sm break-words">
                        {item.content && (
                          <div className="mb-3">
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

                return (
                  <div
                    key={i}
                    className={`flex animate-message-in ${item.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    {item.role === "assistant" && (
                      <div className="w-7 h-7 bg-brand-100 rounded-lg flex items-center justify-center flex-shrink-0 mr-2 md:mr-3 mt-0.5 hidden md:flex">
                        <span className="text-xs font-bold text-brand-700">G</span>
                      </div>
                    )}
                    <div
                      className={`max-w-[90%] md:max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed break-words ${
                        item.role === "user"
                          ? "bg-[#1F1D1A] text-[#FDFBF7] rounded-br-md whitespace-pre-wrap"
                          : "bg-surface border border-gray-200 text-gray-800 rounded-bl-md shadow-sm"
                      }`}
                    >
                      {item.role === "assistant" && item.content ? (
                        <MarkdownContent content={item.content} />
                      ) : item.role === "assistant" && !item.content && (item.artifactIds?.length || item.fileAttachments?.length) ? (
                        null /* Artifact/file-only message — cards rendered below */
                      ) : item.content ? (
                        item.content
                      ) : (
                        <TypingIndicator />
                      )}
                      {/* Inline artifact cards */}
                      {item.role === "assistant" && item.artifactIds && item.artifactIds.length > 0 && (
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
                      {item.role === "assistant" && item.fileAttachments && item.fileAttachments.length > 0 && (
                        <div className={`flex flex-col gap-2 ${item.content || (item.artifactIds && item.artifactIds.length > 0) ? "mt-3" : ""}`}>
                          {item.fileAttachments.map((file) => (
                            <FileAttachmentCard key={file.file_id} file={file} />
                          ))}
                        </div>
                      )}
                      {item.role === "assistant" && typeof item.responseTimeSeconds === "number" && (
                        <div className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-gray-400">
                          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l3.5 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                          </svg>
                          <span>{formatResponseTime(item.responseTimeSeconds)}</span>
                        </div>
                      )}
                      {item.role === "user" && item.fileNames && item.fileNames.length > 0 && (
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
                                  className="w-16 h-16 rounded-lg overflow-hidden border border-white/20 hover:border-white/40 transition-colors flex-shrink-0"
                                >
                                  <img src={src} alt={name} className="w-full h-full object-cover" />
                                </button>
                              );
                            }
                            return (
                              <span key={fi} className="inline-flex items-center gap-1 bg-brand-500/30 text-brand-100 text-xs px-2 py-0.5 rounded-md">
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="m18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13" />
                                </svg>
                                {name}
                              </span>
                            );
                          })}
                        </div>
                      )}
                    </div>
                    {item.role === "user" && (
                      <div className="w-7 h-7 bg-gray-200 rounded-lg flex items-center justify-center flex-shrink-0 ml-2 md:ml-3 mt-0.5 hidden md:flex">
                        <svg className="w-3.5 h-3.5 text-gray-600" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
                        </svg>
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Fallback indicator before any events arrive */}
              {isStreaming && !isRecovering && items.length > 0 && items[items.length - 1].role === "user" && (
                <div className="flex justify-start animate-message-in">
                  <div className="w-7 h-7 bg-brand-100 rounded-lg flex items-center justify-center flex-shrink-0 mr-2 md:mr-3 mt-0.5 hidden md:flex">
                    <span className="text-xs font-bold text-brand-700">G</span>
                  </div>
                  <StatusPill
                    message={
                      accountInfo?.runtime === "opencode"
                        ? "Waiting for OpenCode events..."
                        : "Waiting for agent events..."
                    }
                    elapsedSeconds={streamElapsedSeconds}
                  />
                </div>
              )}

              {/* Recovery polling indicator */}
              {isRecovering && (
                <div className="flex justify-start animate-message-in">
                  <div className="w-7 h-7 bg-brand-100 rounded-lg flex items-center justify-center flex-shrink-0 mr-2 md:mr-3 mt-0.5 hidden md:flex">
                    <span className="text-xs font-bold text-brand-700">G</span>
                  </div>
                  <div className="bg-surface border border-gray-200 text-gray-800 rounded-2xl rounded-bl-md shadow-sm px-4 py-2.5 text-sm">
                    <span className="flex items-center gap-2 text-gray-500">
                      <svg className="animate-spin w-3.5 h-3.5 text-brand-500" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Still working on your request...
                    </span>
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Input */}
          <div className="sticky bottom-0 border-t border-gray-200 bg-surface px-3 md:px-6 py-3 md:py-4 shrink-0">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
              className="max-w-3xl mx-auto"
            >
              {renderPendingFiles()}

              <div className="relative flex items-end bg-gray-50 border border-gray-200 rounded-2xl focus-within:ring-2 focus-within:ring-brand-500 focus-within:border-brand-500 transition-shadow">
                <div className="flex items-center gap-1.5 ml-1 flex-shrink-0">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isStreaming}
                    className="p-2.5 text-gray-400 hover:text-gray-600 disabled:opacity-40 transition-colors flex-shrink-0"
                    title="Attach files"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m18.375 12.739-7.693 7.693a4.5 4.5 0 0 1-6.364-6.364l10.94-10.94A3 3 0 1 1 19.5 7.372L8.552 18.32m.009-.01-.01.01m5.699-9.941-7.81 7.81a1.5 1.5 0 0 0 2.112 2.13" />
                    </svg>
                  </button>
                </div>

                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={isStreaming ? "Agent is working... press Esc or Stop to interrupt" : "Ask the agent something..."}
                  disabled={isStreaming}
                  rows={1}
                  className="flex-1 bg-transparent px-2 py-3 text-sm text-gray-900 placeholder-gray-400 focus:outline-none disabled:opacity-50 resize-none overflow-hidden"
                  style={{ maxHeight: "160px" }}
                />
                {isStreaming ? (
                  <button
                    type="button"
                    onClick={handleStop}
                    className="m-1.5 bg-red-500 hover:bg-red-600 text-white p-2 rounded-xl transition-colors press-scale flex-shrink-0"
                    title="Stop agent (Esc)"
                  >
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                      <rect x="6" y="6" width="12" height="12" rx="2" />
                    </svg>
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!input.trim() && pendingFiles.length === 0}
                    className="m-1.5 bg-accent-200 hover:bg-accent-300 disabled:opacity-40 disabled:hover:bg-accent-200 text-accent-on p-2 rounded-xl transition-colors press-scale flex-shrink-0"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
                    </svg>
                  </button>
                )}
              </div>
              <div className="mt-2 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div className="flex min-w-0 justify-center md:justify-start">
                  {renderModelPicker(true)}
                </div>
                <p className="text-xs text-gray-400 text-center md:text-right hidden md:block">
                  Press Enter to send, Shift+Enter for new line. {isStreaming ? "Press Esc to stop the agent." : "Drag & drop, paste, or click the clip to attach files."}
                </p>
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
      const last = updated[updated.length - 1];
      const newText = last?.role === "assistant" && event.append
        ? last.content + event.text
        : last?.role === "assistant"
        ? (last.content ? last.content + "\n\n" + event.text : event.text)
        : event.text;

      const clarify = extractClarifyBlock(newText);
      if (clarify) {
        if (last?.role === "assistant") {
          if (clarify.before) {
            updated[updated.length - 1] = { ...last, content: clarify.before };
          } else {
            updated.pop();
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
        if (last?.role === "assistant") {
          updated[updated.length - 1] = { ...last, content: newText };
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
            <p className="text-sm font-medium text-gray-700 mb-2">{q.question}</p>
            <div className="flex flex-wrap gap-2 mb-2">
              {q.options.map((opt) => {
                const isSelected = selected.has(opt.label);
                return (
                  <button
                    key={opt.label}
                    type="button"
                    disabled={submitted}
                    onClick={() => toggleOption(qIndex, opt.label, q.multiSelect)}
                    className={`
                      inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium
                      border transition-all duration-150
                      ${submitted && !isSelected ? "opacity-40" : ""}
                      ${isSelected
                        ? "bg-brand-50 border-brand-300 text-brand-700"
                        : "bg-gray-50 border-gray-200 text-gray-600 hover:bg-brand-50/50 hover:border-brand-200"
                      }
                      ${submitted ? "cursor-default" : "cursor-pointer"}
                    `}
                    title={opt.description}
                  >
                    {isSelected && (
                      <svg className="w-3 h-3 text-brand-600" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                      </svg>
                    )}
                    {opt.label}
                  </button>
                );
              })}
            </div>
            {!submitted && q.options.some((o) => o.description) && (
              <div className="space-y-0.5 mb-2">
                {q.options.filter((o) => o.description).map((opt) => (
                  <p key={opt.label} className="text-xs text-gray-400">
                    <span className="font-medium text-gray-500">{opt.label}</span> \u2014 {opt.description}
                  </p>
                ))}
              </div>
            )}
            {!submitted && (
              <div className="mt-2">
                <input
                  type="text"
                  placeholder="Other \u2014 type your own..."
                  value={otherTexts[qIndex] || ""}
                  onChange={(e) => setOtherText(qIndex, e.target.value)}
                  className="w-full text-xs px-3 py-1.5 border border-gray-200 rounded-lg bg-gray-50 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-accent-200 focus:border-accent-200"
                />
              </div>
            )}
          </div>
        );
      })}

      {submitted ? (
        <div className="flex items-center gap-1.5 text-xs text-green-600">
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
          </svg>
          Selection submitted
        </div>
      ) : (
        <button
          type="button"
          disabled={!hasAnySelection}
          onClick={handleSubmit}
          className="inline-flex items-center gap-1.5 px-4 py-1.5 bg-accent-200 hover:bg-accent-300 disabled:opacity-40 disabled:hover:bg-accent-200 text-accent-on text-xs font-medium rounded-lg transition-colors press-scale"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
          </svg>
          Submit
        </button>
      )}
    </div>
  );
}

/** Renders a group of tool call steps as a compact collapsible timeline */
function StatusPill({ message, elapsedSeconds }: { message: string; elapsedSeconds?: number }) {
  const suffix = typeof elapsedSeconds === "number" && elapsedSeconds > 0 ? ` · ${elapsedSeconds}s` : "";
  return (
    <div className="inline-flex max-w-[90vw] items-center gap-2 rounded-full border border-gray-200 bg-surface px-3 py-1.5 text-xs text-gray-500 shadow-sm">
      <svg className="h-3.5 w-3.5 animate-spin text-brand-500" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span className="truncate max-w-[560px]">{message}{suffix}</span>
    </div>
  );
}

function StatusLine({ message, elapsedSeconds }: { message: string; elapsedSeconds?: number }) {
  return (
    <div className="flex justify-start animate-message-in">
      <div className="w-0 md:w-7 flex-shrink-0 mr-0 md:mr-3" />
      <div className="max-w-[90%] md:max-w-[75%]">
        <StatusPill message={message} elapsedSeconds={elapsedSeconds} />
      </div>
    </div>
  );
}

function StepsGroup({ steps }: { steps: Step[] }) {
  const [expanded, setExpanded] = useState(false);
  const allDone = steps.every((s) => s.status === "done" || s.status === "error");
  const hasError = steps.some((s) => s.status === "error");
  const doneCount = steps.filter((s) => s.status === "done" || s.status === "error").length;
  const runningStep = steps.find((s) => s.status === "running");

  return (
    <div className="flex justify-start animate-message-in">
      <div className="w-0 md:w-7 flex-shrink-0 mr-0 md:mr-3" />
      <div className="max-w-[90%] md:max-w-[75%] w-full">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-700 transition-colors py-1 group"
        >
          {allDone ? (
            hasError ? (
              <span className="w-4 h-4 rounded-full bg-red-100 flex items-center justify-center">
                <svg className="w-2.5 h-2.5 text-red-500" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </span>
            ) : (
              <span className="w-4 h-4 rounded-full bg-green-100 flex items-center justify-center">
                <svg className="w-2.5 h-2.5 text-green-600" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              </span>
            )
          ) : (
            <svg className="animate-spin w-3.5 h-3.5 text-brand-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}

          <span className="font-medium">
            {allDone
              ? `Used ${steps.length} tool${steps.length > 1 ? "s" : ""}`
              : runningStep
                ? <>Running <span className="text-gray-600">{formatToolName(runningStep.name || "tool")}</span>{runningStep.input ? <span className="text-gray-400 font-normal ml-1 truncate max-w-[300px] inline-block align-bottom" title={runningStep.input}>{runningStep.input}</span> : null} ({doneCount}/{steps.length})</>
                : `Running ${steps.length} tool${steps.length > 1 ? "s" : ""}... (${doneCount}/${steps.length})`}
          </span>

          <svg
            className={`w-3 h-3 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2}
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
          </svg>
        </button>

        {expanded && (
          <div className="mt-1 ml-1 border-l-2 border-gray-200 pl-3 space-y-1.5 pb-1 animate-expand">
            {steps.map((step, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                {step.status === "running" ? (
                  <svg className="animate-spin w-3 h-3 text-brand-500 flex-shrink-0" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                ) : step.status === "error" ? (
                  <svg className="w-3 h-3 text-red-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                  </svg>
                ) : (
                  <svg className="w-3 h-3 text-green-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                  </svg>
                )}
                <span className={`font-mono truncate ${step.status === "error" ? "text-red-600" : "text-gray-600"}`}>
                  {formatToolName(step.name || "Unknown")}
                </span>
                {step.input && (
                  <span className="text-gray-400 truncate flex-1 min-w-0" title={step.input}>
                    {step.input}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
