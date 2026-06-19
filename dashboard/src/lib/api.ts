// Base path for preview deployments (e.g. /pr/27). Empty in production.
export const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

// Keep browser API calls same-origin so Next.js middleware can inject auth
// headers before rewrites proxy requests to the Python backend.
const API_BASE = basePath;

export interface Conversation {
  _id: string;
  conversation_id: string;
  source: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  status: string;
  metadata: Record<string, string>;
  prompt: string;
  model: string;
  total_turns: number;
  final_response: string;
  title: string | null;
  topic: string | null;
  confidence: {
    resolved: boolean | null;
    confidence_score: number | null;
    category: string;
    reasoning: string;
  } | null;
  error: string | null;
  cost: {
    input_tokens: number;
    output_tokens: number;
    agent_cost_usd: number;
    confidence_cost_usd: number;
    total_cost_usd: number;
  } | null;
  savings: {
    estimated_human_duration_minutes: number;
    expertise_category: string;
    median_hourly_wage_usd: number;
    estimated_human_cost_usd: number;
    savings_usd: number;
  } | null;
  claude_account?: string | null;
  project_id?: string | null;
  title_edited?: boolean;
  deleted?: boolean;
  messages?: Array<{
    role: "user" | "assistant";
    content: string;
    timestamp?: string;
  }>;
}

export interface Turn {
  _id: string;
  conversation_id: string;
  turn_number: number;
  timestamp: string;
  message_type: string;
  text_blocks?: Array<{ text: string; _truncated?: boolean }>;
  tool_calls?: Array<{
    tool_name: string;
    tool_use_id: string;
    input: string;
    _input_truncated?: boolean;
  }>;
  tool_results?: Array<{
    tool_use_id: string;
    is_error: boolean;
    output: string;
    _output_truncated?: boolean;
  }>;
}

/** Artifact persisted in MongoDB — returned alongside turns for history replay */
export interface PersistedArtifact {
  _id: string;
  conversation_id: string;
  artifact_id: string;
  title: string;
  language: string;
  version: number;
  timestamp: string;
  artifact_type: "code" | "file";
  content?: string;       // present for code artifacts
  file_url?: string;      // present for file artifacts
  file_size?: number;     // present for file artifacts
  file_type?: string;     // present for file artifacts
}

export interface ConversationListResponse {
  conversations: Conversation[];
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
}

export interface ConversationDetailResponse {
  conversation: Conversation;
  turns: Turn[];
  artifacts?: PersistedArtifact[];
}

export interface StatsResponse {
  total_conversations: number;
  by_source: Record<string, number>;
  by_category: Record<string, number>;
  by_status: Record<string, number>;
}

export interface DailyCostEntry {
  date: string;
  total_cost_usd: number;
  agent_cost_usd: number;
  confidence_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  conversations: number;
  estimated_human_cost_usd: number;
  savings_usd: number;
  estimated_human_duration_minutes: number;
}

export interface CostStatsResponse {
  daily: DailyCostEntry[];
  total_cost_usd: number;
  total_conversations: number;
  avg_cost_per_conversation: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_estimated_human_cost_usd: number;
  total_savings_usd: number;
  total_estimated_human_minutes: number;
  savings_percentage: number;
}

export interface TokenUsageRow {
  type: "user" | "flow";
  name: string;
  flow_id?: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  conversations: number;
}

export interface TokenUsageResponse {
  rows: TokenUsageRow[];
  totals: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    conversations: number;
  };
}

export async function fetchTokenUsage(params: {
  days?: number;
  type?: string;
  name?: string;
} = {}): Promise<TokenUsageResponse> {
  const searchParams = new URLSearchParams();
  if (params.days) searchParams.set("days", String(params.days));
  if (params.type) searchParams.set("type", params.type);
  if (params.name) searchParams.set("name", params.name);
  const res = await fetch(`${API_BASE}/api/token-usage?${searchParams}`);
  if (!res.ok) throw new Error(`Failed to fetch token usage: ${res.status}`);
  return res.json();
}

export async function fetchConversations(params: {
  page?: number;
  source?: string;
  category?: string;
  status?: string;
  search?: string;
  person?: string;
  topic?: string;
} = {}): Promise<ConversationListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page) searchParams.set("page", String(params.page));
  if (params.source) searchParams.set("source", params.source);
  if (params.category) searchParams.set("category", params.category);
  if (params.status) searchParams.set("status", params.status);
  if (params.search) searchParams.set("search", params.search);
  if (params.person) searchParams.set("person", params.person);
  if (params.topic) searchParams.set("topic", params.topic);

  const res = await fetch(`${API_BASE}/api/conversations?${searchParams}`);
  if (!res.ok) throw new Error(`Failed to fetch conversations: ${res.status}`);
  return res.json();
}

export async function fetchConversation(id: string): Promise<ConversationDetailResponse> {
  const res = await fetch(`${API_BASE}/api/conversations/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch conversation: ${res.status}`);
  return res.json();
}

export async function fetchStats(): Promise<StatsResponse> {
  const res = await fetch(`${API_BASE}/api/stats`);
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.status}`);
  return res.json();
}

export async function fetchCostStats(days: number = 30): Promise<CostStatsResponse> {
  const res = await fetch(`${API_BASE}/api/cost-stats?days=${days}`);
  if (!res.ok) throw new Error(`Failed to fetch cost stats: ${res.status}`);
  return res.json();
}

export async function fetchPersons(): Promise<{ persons: string[] }> {
  const res = await fetch(`${API_BASE}/api/persons`);
  if (!res.ok) throw new Error(`Failed to fetch persons: ${res.status}`);
  return res.json();
}

export async function generateTitles(): Promise<{ processed: number; message: string }> {
  const res = await fetch(`${API_BASE}/api/conversations/generate-titles`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to generate titles: ${res.status}`);
  return res.json();
}

export interface Skill {
  name: string;
  description: string;
  has_extra_files: boolean;
  files: string[];
}

export interface SkillDetailResponse {
  name: string;
  content: string;
  extra_files: Record<string, string>;
}

export async function fetchSkills(): Promise<{ skills: Skill[] }> {
  const res = await fetch(`${API_BASE}/api/skills`);
  if (!res.ok) throw new Error(`Failed to fetch skills: ${res.status}`);
  return res.json();
}

export async function fetchSkill(name: string): Promise<SkillDetailResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Failed to fetch skill: ${res.status}`);
  return res.json();
}

export interface SkillCommit {
  sha: string;
  author: string;
  email: string;
  date: string;
  message: string;
}

export interface SkillHistoryResponse {
  name: string;
  commits: SkillCommit[];
}

export interface SkillVersionResponse {
  name: string;
  sha: string;
  content: string;
}

export async function fetchSkillHistory(name: string): Promise<SkillHistoryResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/history`);
  if (!res.ok) throw new Error(`Failed to fetch skill history: ${res.status}`);
  return res.json();
}

export async function fetchSkillVersion(name: string, sha: string): Promise<SkillVersionResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/version/${sha}`);
  if (!res.ok) throw new Error(`Failed to fetch skill version: ${res.status}`);
  return res.json();
}

export interface SkillDiffResponse {
  name: string;
  from_sha: string;
  to_sha: string;
  diff: string;
}

export async function fetchSkillDiff(name: string, fromSha: string, toSha = "HEAD"): Promise<SkillDiffResponse> {
  const params = new URLSearchParams({ from: fromSha, to: toSha });
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/diff?${params}`);
  if (!res.ok) throw new Error(`Failed to fetch diff: ${res.status}`);
  return res.json();
}

export interface McpServer {
  name: string;
  type: string;
  description?: string;
  url?: string;
  command?: string;
  args?: string[];
  env_keys?: string[];
}

export async function fetchMcpServers(): Promise<{ servers: McpServer[] }> {
  const res = await fetch(`${API_BASE}/api/mcp-servers`);
  if (!res.ok) throw new Error(`Failed to fetch MCP servers: ${res.status}`);
  return res.json();
}

// --- Flows (scheduled/recurring automations & webhook-triggered flows) ---

export interface WebhookConfig {
  auth_method: "bearer_token" | "hmac_sha256" | "none";
  auth_secret?: string;
  signature_header?: string;
}

export interface Flow {
  flow_id: string;
  name: string;
  description: string;
  prompt: string;
  model?: string | null;
  trigger_type?: "scheduled" | "webhook";
  schedule_type: "once" | "recurring";
  frequency: string;
  cron: string | null;
  timezone: string;
  start_time: string | null;
  end_time: string | null;
  channel_id: string;
  channel_name: string;
  prompt_template?: string;
  webhook_config?: WebhookConfig;
  status: "active" | "paused" | "completed";
  labels: string[];
  visibility?: "private" | "shared";
  created_by: {
    user_id?: string;
    user_name?: string;
    source?: string;
  };
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  next_run_at: string | null;
  run_count: number;
  creation_conversation_id: string | null;
  last_run_conversation_id: string | null;
  last_error: string | null;
}

export interface WebhookLog {
  log_id: string;
  flow_id: string;
  flow_name: string;
  received_at: string;
  headers: Record<string, string>;
  body: unknown;
  auth_method: string;
  auth_result: "success" | "failed" | "skipped" | "pending";
  execution_status: "pending" | "running" | "completed" | "error" | "skipped";
  conversation_id: string | null;
  response_status_code: number | null;
  error: string | null;
  duration_ms: number | null;
}

export async function fetchFlows(
  status?: string,
  triggerType?: string,
): Promise<{ flows: Flow[] }> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (triggerType) params.set("trigger_type", triggerType);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${API_BASE}/api/flows${qs}`);
  if (!res.ok) throw new Error(`Failed to fetch flows: ${res.status}`);
  return res.json();
}

export async function fetchFlow(id: string): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch flow: ${res.status}`);
  return res.json();
}

export async function createFlow(data: Partial<Flow>): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to create flow: ${res.status}`);
  return res.json();
}

export async function updateFlow(id: string, updates: Partial<Flow>): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update flow: ${res.status}`);
  return res.json();
}

export async function deleteFlow(id: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to delete flow: ${res.status}`);
  return res.json();
}

export async function pauseFlow(id: string): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}/pause`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to pause flow: ${res.status}`);
  return res.json();
}

export async function resumeFlow(id: string): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}/resume`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to resume flow: ${res.status}`);
  return res.json();
}

export async function triggerFlow(id: string): Promise<{ triggered: boolean }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}/run-now`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to trigger flow: ${res.status}`);
  return res.json();
}

export async function fetchFlowRuns(id: string, limit: number = 20): Promise<{ runs: Conversation[] }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}/runs?limit=${limit}`);
  if (!res.ok) throw new Error(`Failed to fetch flow runs: ${res.status}`);
  return res.json();
}

export async function updateFlowLabels(
  id: string,
  labels: string[],
): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}/labels`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ labels }),
  });
  if (!res.ok) throw new Error(`Failed to update flow labels: ${res.status}`);
  return res.json();
}

export async function fetchLabels(): Promise<{ labels: string[] }> {
  const res = await fetch(`${API_BASE}/api/labels`);
  if (!res.ok) throw new Error(`Failed to fetch labels: ${res.status}`);
  return res.json();
}

// --- Webhook Logs ---

export async function fetchWebhookLogs(
  flowId?: string,
  limit: number = 50,
): Promise<{ logs: WebhookLog[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (flowId) params.set("flowId", flowId);
  const res = await fetch(`${API_BASE}/api/webhook-logs?${params.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch webhook logs: ${res.status}`);
  return res.json();
}

export async function fetchWebhookLog(
  logId: string,
): Promise<{ log: WebhookLog }> {
  const res = await fetch(`${API_BASE}/api/webhook-logs/${logId}`);
  if (!res.ok) throw new Error(`Failed to fetch webhook log: ${res.status}`);
  return res.json();
}

// --- Conversation Management (rename, delete) ---

export async function updateConversation(
  id: string,
  updates: { title?: string },
): Promise<{ conversation: Conversation }> {
  const res = await fetch(`${API_BASE}/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to update conversation: ${res.status}`);
  }
  return res.json();
}

export async function deleteConversation(id: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${API_BASE}/api/conversations/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to delete conversation: ${res.status}`);
  }
  return res.json();
}

// --- Projects (chat organization) ---

export interface Project {
  _id: string;
  project_id: string;
  name: string;
  description: string | null;
  color: string | null;
  icon: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  deleted: boolean;
  conversation_count?: number;
}

export async function fetchProjects(): Promise<{ projects: Project[] }> {
  const res = await fetch(`${API_BASE}/api/projects`);
  if (!res.ok) throw new Error(`Failed to fetch projects: ${res.status}`);
  return res.json();
}

export async function createProject(data: {
  name: string;
  description?: string;
  color?: string;
  icon?: string;
}): Promise<{ project: Project }> {
  const res = await fetch(`${API_BASE}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to create project: ${res.status}`);
  }
  return res.json();
}

export async function updateProject(
  id: string,
  updates: Partial<Pick<Project, "name" | "description" | "color" | "icon">>,
): Promise<{ project: Project }> {
  const res = await fetch(`${API_BASE}/api/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Failed to update project: ${res.status}`);
  return res.json();
}

export async function deleteProject(id: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${API_BASE}/api/projects/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to delete project: ${res.status}`);
  return res.json();
}

export async function fetchProject(id: string): Promise<{
  project: Project;
  conversations: Conversation[];
}> {
  const res = await fetch(`${API_BASE}/api/projects/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch project: ${res.status}`);
  return res.json();
}

export async function assignConversationToProject(
  conversationId: string,
  projectId: string,
): Promise<{ project_id: string }> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/project`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) throw new Error(`Failed to assign to project: ${res.status}`);
  return res.json();
}

export async function removeConversationFromProject(
  conversationId: string,
): Promise<{ project_id: null }> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/project`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to remove from project: ${res.status}`);
  return res.json();
}

// --- Pinned Conversations ---

export interface PinnedConversationsResponse {
  conversations: Conversation[];
  pinned_ids: string[];
}

export async function fetchPinnedConversations(): Promise<PinnedConversationsResponse> {
  const res = await fetch(`${API_BASE}/api/conversations/pinned`);
  if (!res.ok) throw new Error(`Failed to fetch pinned conversations: ${res.status}`);
  return res.json();
}

export async function pinConversation(conversationId: string): Promise<{ pinned: boolean }> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/pin`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to pin conversation: ${res.status}`);
  }
  return res.json();
}

export async function unpinConversation(conversationId: string): Promise<{ pinned: boolean }> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/pin`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to unpin conversation: ${res.status}`);
  return res.json();
}

// --- Pool Status ---

export interface PoolStatus {
  pool_size: number;
  available: number;
  in_use: number;
  warming: number;
  queue_depth: number;
  accounts: string[];
  accounts_on_cooldown: string[];
  account_distribution: Record<string, number>;
  opencode?: {
    enabled: boolean;
    pool_size: number;
    configured_models: string[];
    active_sessions: number;
    total_available: number;
    total_warming: number;
    models: Array<{
      model: string;
      enabled: boolean;
      pool_size: number;
      available: number;
      warming: number;
    }>;
  };
}

export async function fetchPoolStatus(): Promise<PoolStatus> {
  const res = await fetch(`${API_BASE}/api/pool-status`);
  if (!res.ok) throw new Error(`Failed to fetch pool status: ${res.status}`);
  return res.json();
}

// --- File serving ---

/** Get the full URL for a file artifact by its file ID */
export function getFileUrl(fileId: string): string {
  return `${API_BASE}/api/files/${fileId}`;
}

/** Extract file ID from a /api/files/{id} path */
export function extractFileIdFromUrl(url: string): string | null {
  const match = url.match(/\/api\/files\/(.+)$/);
  return match ? match[1] : null;
}

// --- Chat ---

export interface ClarifyOption {
  label: string;
  description?: string;
}

export interface ClarifyQuestion {
  question: string;
  options: ClarifyOption[];
  multiSelect: boolean;
}

export type ChatEvent =
  | { type: "text"; text: string; append?: boolean }
  | { type: "status"; message: string; elapsed_seconds?: number }
  | { type: "tool_call"; name: string; tool_use_id: string; input?: string }
  | { type: "tool_result"; tool_use_id: string; is_error: boolean }
  | { type: "turn"; turn_number: number }
  | { type: "conversation_id"; conversation_id: string }
  | { type: "clarify"; questions: ClarifyQuestion[] }
  | { type: "error"; error: string }
  | { type: "account_info"; account_type?: "round_robin"; account_email?: string; pool_available?: number; pool_size?: number; pool_warming?: number; active_sessions?: number; warm_session_used?: boolean; runtime?: string; provider?: string; model?: string }
  | { type: "artifact"; artifact_id: string; title: string; content: string; language: string; version: number }
  | { type: "file_artifact"; artifact_id: string; title: string; language: string; file_url: string; file_size: number; file_type: string; version: number; previews?: string[] }
  | { type: "file"; file_id: string; name: string; url: string; mime_type: string; size: number }

export interface AgentModel {
  id: string;
  provider_id: string;
  model_id: string;
  label: string;
  context_limit?: number | null;
  supports_attachments: boolean;
  supports_reasoning: boolean;
  status: string;
  recommended?: boolean;
  cost?: {
    input?: number | null;
    output?: number | null;
    cache_read?: number | null;
    cache_write?: number | null;
  };
}

export interface AgentModelsResponse {
  default_model: string | null;
  models: AgentModel[];
}

export async function fetchAgentModels(): Promise<AgentModelsResponse> {
  const res = await fetch(`${API_BASE}/api/agent-models`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to fetch agent models: ${res.status}`);
  }
  return res.json();
}


export interface ChatFile {
  name: string;
  mimetype: string;
  type: "image" | "text" | "binary";
  data: string; // base64 for images/binary, raw text for text files
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export async function* streamChat(
  message: string,
  conversationHistory?: ChatMessage[],
  files?: ChatFile[],
  conversationId?: string,
  userEmail?: string,
  signal?: AbortSignal,
  selectedModel?: string,
): AsyncGenerator<ChatEvent, void, unknown> {
  const body: Record<string, unknown> = { message };
  if (conversationHistory?.length) body.conversation_history = conversationHistory;
  if (files?.length) body.files = files;
  if (conversationId) body.conversation_id = conversationId;
  if (userEmail) body.user_email = userEmail;
  if (selectedModel) body.model = selectedModel;

  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) throw new Error(`Chat request failed: ${res.status}`);
  if (!res.body) throw new Error("No response body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6);
          if (data === "[DONE]") return;
          try {
            const parsed = JSON.parse(data);
            if (parsed.error) throw new Error(parsed.error);
            if (parsed.type) {
              yield parsed as ChatEvent;
            } else if (parsed.text) {
              // Backward compat: old format without type field
              yield { type: "text", text: parsed.text };
            }
          } catch (e) {
            if (e instanceof Error && e.message !== "Unexpected end of JSON input") {
              throw e;
            }
            // Skip incomplete JSON
          }
        }
      }
    }
  } finally {
    // Ensure the reader is released when the generator is closed (e.g. on abort)
    try {
      reader.cancel();
    } catch {
      // Ignore cancel errors
    }
  }
}
