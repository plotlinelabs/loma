import type { AgentSwitchEvent, AttributedMessage, BlockedCall } from "./agent-attribution";

// Base path for preview deployments (e.g. /pr/27). Empty in production.
export const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

// Keep browser API calls same-origin so Next.js middleware can inject auth
// headers before rewrites proxy requests to the Python backend.
const API_BASE = basePath;

export interface HumanTask {
  kind: "approval" | "information";
  details: string;
  ticket_url: string;
  source_conversation_id: string;
  requested_by: string;
  state: "pending" | "answered";
  version: number;
  decision: "approve" | "reject" | "provide_information" | null;
  response: string | null;
  responded_by: string | null;
  resume_state: "waiting" | "queued" | "running" | "completed" | "needs_review";
}

export interface Conversation {
  human_task?: HumanTask;
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
  task_status?: "todo" | "active" | "done" | null;
  /** Attachments staged with a board-task draft (cleared once started) */
  draft_files?: ChatFile[];
  tool_config?: ToolConfig | null;
  messages?: AttributedMessage[];
  /** Agent changes part-way through the thread (picker, "Loma", Slack naming). */
  agent_events?: AgentSwitchEvent[];
  /** Tool/skill calls the active agent's scope blocked. */
  blocked_calls?: BlockedCall[];
  blocked_call_count?: number;
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
  /** An agent_id, or "loma" for runs answered by the default agent only. */
  agent?: string;
  /** Only runs where the agent's scope blocked a tool or skill call. */
  blocked?: boolean;
} = {}): Promise<ConversationListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page) searchParams.set("page", String(params.page));
  if (params.source) searchParams.set("source", params.source);
  if (params.category) searchParams.set("category", params.category);
  if (params.status) searchParams.set("status", params.status);
  if (params.search) searchParams.set("search", params.search);
  if (params.person) searchParams.set("person", params.person);
  if (params.topic) searchParams.set("topic", params.topic);
  if (params.agent) searchParams.set("agent", params.agent);
  if (params.blocked) searchParams.set("blocked", "1");

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
  source?: GoogleSkillSource;
  slug?: string;
  name: string;
  description: string;
  tags?: string[];
  has_extra_files: boolean;
  files: string[];
  file_details?: SkillFile[];
  updated_at?: string;
  created_by?: string;
  scope?: "system" | "personal" | "workspace";
  folder?: string | null;
  folder_source?: "manual" | "auto" | null;
}

export interface SkillFile {
  path: string;
  kind: "inline_text" | "local_asset";
  content_type?: string;
  size_bytes?: number;
  content_hash?: string;
  original_filename?: string;
}

export interface SkillDetailResponse {
  source?: GoogleSkillSource;
  slug?: string;
  name: string;
  description?: string;
  tags?: string[];
  content: string;
  extra_files: Record<string, string>;
  files: SkillFile[];
  assets?: SkillFile[];
  updated_at?: string;
  created_by?: string;
  scope?: "system" | "personal" | "workspace";
  folder?: string | null;
  folder_source?: "manual" | "auto" | null;
}

// ── Tool/Skill picker catalog ──────────────────────────────────────────────

export interface ToolConfig {
  enabled_skills?: string[] | null;
  enabled_tools?: string[] | null;
}

export interface AvailableTool {
  id: string;
  name: string;
  group: "built-in" | "integrations";
  description?: string;
}

export interface AvailableSkill {
  slug: string;
  name: string;
  description: string;
  tags?: string[];
  scope?: "workspace" | "personal" | "system";
  folder?: string | null;
}

export interface AvailableToolsResponse {
  tools: AvailableTool[];
  skills: AvailableSkill[];
}

export async function fetchAvailableTools(): Promise<AvailableToolsResponse> {
  const res = await fetch(`${API_BASE}/api/available-tools`);
  if (!res.ok) throw new Error(`Failed to fetch available tools: ${res.status}`);
  return res.json();
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

export async function createSkill(payload: { slug: string; content: string }): Promise<SkillDetailResponse> {
  const res = await fetch(`${API_BASE}/api/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to create skill: ${res.status}`);
  }
  return res.json();
}

export async function updateSkill(name: string, payload: { content?: string; files?: { path: string; content: string }[]; message?: string }): Promise<SkillDetailResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to update skill: ${res.status}`);
  }
  return res.json();
}

export async function updateSkillFile(name: string, path: string, content: string, baseHash?: string): Promise<SkillDetailResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/files`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content, base_hash: baseHash }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to update skill file: ${res.status}`);
  }
  return res.json();
}

export async function updateSkillScope(name: string, scope: "personal" | "workspace"): Promise<SkillDetailResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/scope`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to update skill scope: ${res.status}`);
  }
  return res.json();
}

export async function deleteSkillFile(name: string, path: string): Promise<SkillDetailResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/files`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to delete skill file: ${res.status}`);
  }
  return res.json();
}

export async function uploadSkillAsset(name: string, path: string, file: File): Promise<SkillDetailResponse> {
  const form = new FormData();
  form.set("path", path);
  form.set("file", file);
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/assets`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to upload skill asset: ${res.status}`);
  }
  return res.json();
}

export async function deleteSkill(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to delete skill: ${res.status}`);
  }
  return res.json();
}

export async function updateSkillFolder(name: string, folder: string | null): Promise<SkillDetailResponse> {
  const res = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(name)}/folder`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to update skill folder: ${res.status}`);
  }
  return res.json();
}

export async function autoOrganizeSkills(): Promise<{ organized: number; total: number; message: string; errors?: string[] }> {
  const res = await fetch(`${API_BASE}/api/skills-organize`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Failed to auto-organize skills: ${res.status}`);
  }
  return res.json();
}

export function skillAssetUrl(name: string, path: string): string {
  return `${API_BASE}/api/skills/${encodeURIComponent(name)}/assets/${path.split("/").map(encodeURIComponent).join("/")}`;
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
  has_auth_secret?: boolean;
  signature_header?: string;
}

export interface SlackConfig {
  allow_bot_messages?: boolean;
}

export interface Flow {
  agent_id?: string | null;
  agent_snapshot?: { context: string; name: string; captured_at: string; configuration_updated_at?: string };
  can_manage?: boolean;
  flow_id: string;
  name: string;
  description: string;
  prompt: string;
  model?: string | null;
  trigger_type?: "scheduled" | "webhook" | "slack";
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
  slack_config?: SlackConfig;
  status: "active" | "paused" | "completed";
  labels: string[];
  visibility?: "private" | "shared";
  run_as?: string;
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
  agentId?: string,
): Promise<{ flows: Flow[] }> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (triggerType) params.set("trigger_type", triggerType);
  if (agentId) params.set("agent_id", agentId);
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
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error || "Could not create schedule. Please try again.");
  }
  return res.json();
}

export async function updateFlow(id: string, updates: Partial<Flow>): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error || "Flow was not saved. Please try again.");
  }
  return res.json();
}

export async function deleteFlow(id: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to delete flow: ${res.status}`);
  return res.json();
}

export async function pauseFlow(id: string): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}/pause`, { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error || "Could not pause schedule. Please try again.");
  }
  return res.json();
}

export async function resumeFlow(id: string): Promise<{ flow: Flow }> {
  const res = await fetch(`${API_BASE}/api/flows/${id}/resume`, { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error || "Could not resume schedule. Please try again.");
  }
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

export async function setConversationShared(
  id: string,
  shared: boolean,
): Promise<{ shared: boolean; conversation_id: string }> {
  const res = await fetch(`${API_BASE}/api/conversations/${id}/share`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shared }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to update sharing: ${res.status}`);
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
  codex?: {
    enabled: boolean;
    pool_size?: number;
    available?: number;
    in_use?: number;
    warming?: number;
    queue_depth?: number;
    accounts?: string[];
    accounts_on_cooldown?: string[];
    accounts_auth_failed?: string[];
    account_distribution?: Record<string, number>;
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
  /** Set by the backend when this model fills a favourites slot. */
  favorite_rank?: number | null;
  favorite_label?: string | null;
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

export async function injectMessage(
  conversationId: string,
  message: string,
): Promise<{ injected: boolean }> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/inject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Injection failed: ${res.status}`);
  }
  return res.json();
}

export async function interruptAgent(
  conversationId: string,
): Promise<{ interrupted: boolean }> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/interrupt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Interrupt failed: ${res.status}`);
  }
  return res.json();
}

/** 409 from /api/chat: the conversation already has a run in progress. */
export class ConversationBusyError extends Error {
  /** The message was handed to the running agent mid-stream. */
  injected: boolean;
  /** The message repeats one the agent is already working on (double submit). */
  duplicate: boolean;

  constructor(message: string, injected: boolean, duplicate: boolean) {
    super(message);
    this.name = "ConversationBusyError";
    this.injected = injected;
    this.duplicate = duplicate;
  }
}

/** 202 from /api/chat: a deploy is in progress, so the server saved the
 * message and will run it once the new version is up. */
export class ConversationQueuedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConversationQueuedError";
  }
}

/** /api/chat refused the request before any run started (e.g. 4xx/5xx).
 * Nothing was saved, so the client should give the text back to the user. */
export class ChatRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ChatRequestError";
    this.status = status;
  }
}

export async function* streamChat(
  message: string,
  conversationHistory?: ChatMessage[],
  files?: ChatFile[],
  conversationId?: string,
  userEmail?: string,
  signal?: AbortSignal,
  selectedModel?: string,
  /** An id selects that agent; null explicitly selects the default agent (and
   *  unpins a conversation's agent); undefined leaves the conversation as is. */
  agentId?: string | null,
  toolConfig?: ToolConfig,
  /** Research read-only and propose a plan for review (agent/plan_mode.py). */
  planMode?: boolean,
): AsyncGenerator<ChatEvent, void, unknown> {
  const body: Record<string, unknown> = { message };
  if (planMode) body.plan_mode = true;
  if (conversationHistory?.length) body.conversation_history = conversationHistory;
  if (files?.length) body.files = files;
  if (conversationId) body.conversation_id = conversationId;
  if (userEmail) body.user_email = userEmail;
  if (selectedModel) body.model = selectedModel;
  if (agentId !== undefined) body.agent_id = agentId || null;
  if (toolConfig) body.tool_config = toolConfig;

  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (res.status === 202) {
    const body = await res.json().catch(() => ({}));
    throw new ConversationQueuedError(body.message || "Queued. Loma is updating.");
  }
  if (!res.ok) {
    // Surface the backend's message (e.g. "restarting for a deploy") instead
    // of a bare status code.
    const body = await res.json().catch(() => ({}));
    if (res.status === 409 && body.busy) {
      throw new ConversationBusyError(body.error || "Agent is busy", !!body.injected, !!body.duplicate);
    }
    throw new ChatRequestError(body.error || `Chat request failed: ${res.status}`, res.status);
  }
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

// ── Tasks board ──────────────────────────────────────────────────────────────

export interface BoardLane {
  id: string;
  name: string;
  order: number;
}

export interface TaskTag {
  id: string;
  name: string;
  color: string;
  created_at: string;
}

export type TaskPriority = "low" | "medium" | "high" | "urgent";

export interface Task {
  human_task?: HumanTask;
  tool_config?: ToolConfig | null;
  conversation_id: string;
  title: string | null;
  prompt: string;
  model: string | null;
  status: string | null;
  task_status: "todo" | "active" | "done";
  task_lane: string | null;
  task_rank: number | null;
  /** Derived column: a lane id, "working", "needs_input", or "done". */
  column: string;
  total_turns: number;
  started_at: string | null;
  finished_at: string | null;
  task_created_at: string | null;
  task_staged_at: string | null;
  task_started_at: string | null;
  task_done_at: string | null;
  task_tag_ids: string[];
  task_priority: TaskPriority | null;
  /** Optional deadline as a date-only "YYYY-MM-DD" string. */
  task_deadline: string | null;
  forked_from_conversation_id: string | null;
  /** Shared board the task sits on; null = its creator's own board. */
  task_board_id?: string | null;
  /** Card the task lives in (card boards only). */
  task_card_id?: string | null;
  /** Creator's email. The creator and the board's owners/editors can message the task. */
  owner?: string | null;
  /** Shared boards: the owner/editor this task is assigned to. Each run uses the sender's accounts. */
  assignee?: string | null;
  /** Shared boards: you starred this task. Private: nobody else sees it. */
  starred?: boolean;
  /** Your own board only: this card is a task you starred on a shared board. */
  star?: TaskStar | null;
}

/** Your private placement of a starred task on your own board. Moving the card,
 * ticking it off or setting its tags, priority or deadline changes only this,
 * never the task on its shared board. On a starred card, `task_tag_ids`,
 * `task_priority` and `task_deadline` are your own values (blank at first). */
export interface TaskStar {
  /** Your lane for it. */
  lane: string;
  /** Done for you (the task may still be open on its board). */
  done: boolean;
  /** Your role on the board the task lives on. */
  role: TaskBoardRole;
  board_id: string;
  board_name: string;
  board_emoji?: string;
  /** Card the task sits in (card boards). */
  card_title?: string | null;
  /** Where the task really is on its board: a lane id, "working", "needs_input" or "done". */
  source_column: string;
  /** You moved it into your lane while the task was live: it stays there
   * until the task moves on (a new run, a finished run, done or staged). */
  parked?: boolean;
}

/** Whether `me` can message (run) a task: its creator, or an owner/editor of the shared board it sits on. */
export function canRunTask(task: Pick<Task, "owner" | "task_board_id"> | null | undefined, me: string | null | undefined,
  boardRole?: TaskBoardRole | null): boolean {
  if (!task || !me) return false;
  if (task.owner === me) return true;
  return !!task.task_board_id && (boardRole === "owner" || boardRole === "editor");
}

export type TaskBoardRole = "owner" | "editor" | "viewer";

export interface TaskBoardMember {
  email: string;
  /** "owner" members are co-owners: they can share, rename and delete the board. */
  role: "owner" | "editor" | "viewer";
}

export interface TaskBoardSummary {
  /** "personal" for the caller's own board. */
  id: string;
  name: string;
  owner: string;
  role: TaskBoardRole;
  shared: boolean;
  /** Card board: columns hold cards, and tasks live inside cards. */
  card_mode?: boolean;
  members: TaskBoardMember[];
  /** Board list only: the caller's tasks on this board that are waiting on them. */
  needs_you?: number;
  /** Shown in the nav. Owners set it on shared boards; Personal is per person. */
  emoji?: string;
}

export type BoardFieldType =
  | "text" | "number" | "date" | "person" | "select" | "multi_select" | "link" | "checkbox";

/** A custom field defined on a card board; cards store values by field id. */
export interface BoardField {
  id: string;
  name: string;
  type: BoardFieldType;
  options: string[];
  show_on_card: boolean;
}

export type CardFieldValue = string | number | boolean | string[] | null;

export interface TaskCardItem {
  card_id: string;
  board_id: string;
  title: string;
  lane: string;
  rank: number;
  fields: Record<string, CardFieldValue>;
  /** Notes for Loma: added to the context of every task inside the card. */
  notes: string;
  /** Values Loma filled in through a task: field id -> who ran it. Cleared when a person edits the value. */
  field_meta?: Record<string, LomaStamp>;
  /** Notes Loma added through a task (kept apart from the user's notes). */
  loma_notes?: LomaNote[];
  created_by?: string | null;
  task_total: number;
  task_done: number;
  task_running: number;
  task_needs_input: number;
}

export interface LomaStamp {
  by: "loma";
  /** Whose run (and accounts) wrote it. */
  run_by: string;
  conversation_id?: string;
  at?: string;
}

export interface LomaNote extends LomaStamp {
  id: string;
  text: string;
}

export type BoardTemplate = "blank" | "deals" | "hiring" | "projects";

export const PERSONAL_BOARD_ID = "personal";

/** `?board=` suffix for board-scoped task endpoints (omitted for the personal board). */
function boardQuery(boardId?: string, prefix = "?"): string {
  return boardId && boardId !== PERSONAL_BOARD_ID ? `${prefix}board=${encodeURIComponent(boardId)}` : "";
}

export interface TasksBoardResponse {
  board?: TaskBoardSummary;
  show_agent_work?: boolean;
  lanes: BoardLane[];
  tags: TaskTag[];
  tasks: Task[];
  counts: Record<string, number>;
  /** Card boards only. */
  fields?: BoardField[];
  cards?: TaskCardItem[];
}

export interface BoardSettings {
  show_agent_work?: boolean;
  prompt: string;
  lanes: BoardLane[];
  /** Global default context (Admin > Settings), resolved for the caller. Read-only. */
  default_context?: string;
  /** Present for shared boards. */
  board?: TaskBoardSummary;
  card_mode?: boolean;
  fields?: BoardField[];
}

export class BoardNotFoundError extends Error {}

export async function fetchTasksBoard(query = "", boardId?: string): Promise<TasksBoardResponse> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("q", query.trim());
  if (boardId && boardId !== PERSONAL_BOARD_ID) params.set("board", boardId);
  const qs = params.toString();
  const res = await fetch(`${API_BASE}/api/tasks${qs ? `?${qs}` : ""}`);
  if (res.status === 404) throw new BoardNotFoundError("Board not found");
  if (!res.ok) throw new Error(`Failed to fetch tasks: ${res.status}`);
  return res.json();
}

async function boardRequest<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const res = await fetch(`${API_BASE}/api/tasks/boards${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `${fallback}: ${res.status}`);
  }
  return res.json();
}

export function fetchTaskBoards(): Promise<{ boards: TaskBoardSummary[] }> {
  return boardRequest("", { method: "GET" }, "Failed to fetch boards");
}

export function createTaskBoard(
  name: string,
  options: { card_mode?: boolean; template?: BoardTemplate } = {},
): Promise<{ board: TaskBoardSummary }> {
  return boardRequest("", { method: "POST", body: JSON.stringify({ name, ...options }) }, "Failed to create board");
}

async function cardRequest<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/tasks/cards${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Card request failed: ${res.status}`);
  }
  return res.json();
}

export function createTaskCard(params: {
  board: string;
  title: string;
  lane?: string;
}): Promise<{ card: TaskCardItem }> {
  return cardRequest("", "POST", params);
}

export function updateTaskCard(
  cardId: string,
  updates: {
    title?: string;
    lane?: string;
    rank?: number;
    /** Partial: only the given field ids change; null clears a value. */
    fields?: Record<string, CardFieldValue>;
    notes?: string;
    /** Delete one of Loma's notes by id. */
    remove_loma_note?: string;
  },
): Promise<{ card: TaskCardItem }> {
  return cardRequest(`/${encodeURIComponent(cardId)}`, "PATCH", updates);
}

export function deleteTaskCard(cardId: string): Promise<{ deleted: boolean; moved: number }> {
  return cardRequest(`/${encodeURIComponent(cardId)}`, "DELETE");
}

export function updateTaskBoard(
  boardId: string,
  updates: { name?: string; members?: TaskBoardMember[]; emoji?: string },
): Promise<{ board: TaskBoardSummary }> {
  return boardRequest(`/${encodeURIComponent(boardId)}`, {
    method: "PATCH", body: JSON.stringify(updates),
  }, "Failed to update board");
}

/** The caller's own board list settings: drag order and Personal's emoji. */
export async function updateBoardPrefs(prefs: { order?: string[]; personal_emoji?: string }): Promise<void> {
  const res = await fetch(`${API_BASE}/api/tasks/boards-prefs`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prefs),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to save board settings: ${res.status}`);
  }
}

export function deleteTaskBoard(boardId: string): Promise<{ deleted: boolean; moved: number }> {
  return boardRequest(`/${encodeURIComponent(boardId)}`, { method: "DELETE" }, "Failed to delete board");
}

/** A task waiting on the caller, on any board they can open. */
export interface NeedsYouTask {
  conversation_id: string;
  title: string | null;
  status: string;
  /** "personal" for the caller's own board. */
  board_id: string;
}

export async function fetchNeedsYouTasks(): Promise<NeedsYouTask[]> {
  const res = await fetch(`${API_BASE}/api/tasks/needs-you`);
  if (!res.ok) throw new Error(`Failed to fetch waiting tasks: ${res.status}`);
  return (await res.json()).tasks ?? [];
}

export async function fetchNeedsInputCount(): Promise<number> {
  const res = await fetch(`${API_BASE}/api/tasks/needs-input-count`);
  if (!res.ok) throw new Error(`Failed to fetch needs-input count: ${res.status}`);
  const data = await res.json();
  return data.count ?? 0;
}

export async function createTask(params: {
  prompt: string;
  title?: string;
  lane?: string;
  model?: string;
  /** Attachments staged with the draft; sent with the first message on start */
  files?: ChatFile[];
  /** Fire immediately: the agent starts running in the background */
  start?: boolean;
  tool_config?: ToolConfig;
  /** Shared board id; omitted = the caller's own board */
  board?: string;
  /** Card the task goes into (required on card boards) */
  card?: string;
}): Promise<{ task: Task }> {
  const res = await fetch(`${API_BASE}/api/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to create task: ${res.status}`);
  }
  return res.json();
}

export async function updateTask(
  conversationId: string,
  updates: {
    task_status?: "todo" | "active" | "done" | null;
    task_lane?: string;
    task_rank?: number;
    prompt?: string;
    title?: string;
    model?: string;
    tool_config?: ToolConfig | null;
    task_tag_ids?: string[];
    task_priority?: TaskPriority | null;
    task_deadline?: string | null;
    /** Move to another board ("personal" = the creator's own board). */
    task_board_id?: string | null;
    /** Card on a card board to move the task into (alone: move to another card). */
    task_card_id?: string | null;
    /** Shared boards: assign to an owner/editor's email, or null to clear. */
    task_assignee?: string | null;
  },
): Promise<{ task: Task | null }> {
  const res = await fetch(`${API_BASE}/api/tasks/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to update task: ${res.status}`);
  }
  return res.json();
}

async function starRequest<T>(conversationId: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/tasks/${conversationId}/star`, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Could not update star: ${res.status}`);
  }
  return res.json();
}

/** Star a shared-board task: it also shows on your own board. Private to you. */
export function starTask(conversationId: string): Promise<{ starred: boolean }> {
  return starRequest(conversationId, "PUT");
}

export function unstarTask(conversationId: string): Promise<{ starred: boolean }> {
  return starRequest(conversationId, "DELETE");
}

/** Your private changes to a starred task on your own board. */
export interface TaskStarUpdate {
  lane?: string;
  done?: boolean;
  rank?: number;
  /** Tags of your own board. */
  tag_ids?: string[];
  priority?: TaskPriority | null;
  /** YYYY-MM-DD. */
  deadline?: string | null;
}

/** Place, tag or prioritize a starred task on your own board. Never changes
 * the task itself: its tags, priority and deadline here are yours only. */
export function updateTaskStar(
  conversationId: string,
  updates: TaskStarUpdate,
): Promise<{ star: { lane: string; done: boolean; rank: number; tag_ids: string[];
  priority: TaskPriority | null; deadline: string | null } }> {
  return starRequest(conversationId, "PATCH", updates);
}

export async function forkTask(
  conversationId: string,
  options: { lane?: string; title?: string; board?: string } = {},
): Promise<{ task: Task }> {
  const res = await fetch(`${API_BASE}/api/tasks/${conversationId}/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to fork task: ${res.status}`);
  }
  return res.json();
}

export async function createTaskTag(name: string, boardId?: string): Promise<{ tag: TaskTag }> {
  const res = await fetch(`${API_BASE}/api/tasks/tags${boardQuery(boardId)}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to create tag: ${res.status}`);
  }
  return res.json();
}

export async function fetchBoardSettings(boardId?: string): Promise<BoardSettings> {
  const res = await fetch(`${API_BASE}/api/tasks/board-settings${boardQuery(boardId)}`);
  if (!res.ok) throw new Error(`Failed to fetch board settings: ${res.status}`);
  return res.json();
}

export async function saveBoardSettings(settings: {
  prompt?: string;
  lanes?: Array<{ id?: string; name: string }>;
  show_agent_work?: boolean;
  /** Card boards only: the full list of custom fields. */
  fields?: Array<Omit<BoardField, "id"> & { id?: string }>;
}, boardId?: string): Promise<BoardSettings & { migrated: number }> {
  const res = await fetch(`${API_BASE}/api/tasks/board-settings${boardQuery(boardId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Failed to save board settings: ${res.status}`);
  }
  return res.json();
}

// ---------- Card board views (saved filters) ----------

export type CardFilterOp =
  | "any_of" | "none_of"
  | "eq" | "gt" | "lt" | "between"
  | "before" | "after" | "on"
  | "contains" | "not_contains"
  | "checked" | "not_checked"
  | "empty" | "not_empty";

export type CardFilterValue = string | number | boolean | null | Array<string | number | null>;

/** One condition on a card board: `field` is a board field id, or "__stage" / "__assignee". */
export interface CardFilter {
  id: string;
  field: string;
  op: CardFilterOp;
  value: CardFilterValue;
}

export type CardFilterMatch = "all" | "any";

/** The filter state a view saves. */
export interface CardViewState {
  filters: CardFilter[];
  match: CardFilterMatch;
  search: string;
  assigned_to_me: boolean;
}

export interface BoardView extends CardViewState {
  id: string;
  board_id: string;
  name: string;
  owner: string;
  /** Everyone on the board sees it (otherwise only its creator). */
  shared: boolean;
  mine: boolean;
  can_edit: boolean;
}

async function viewRequest<T>(path: string, boardId: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/tasks/views${path}${boardQuery(boardId)}`, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `View request failed: ${res.status}`);
  }
  return res.json();
}

export function fetchBoardViews(boardId: string): Promise<{ views: BoardView[] }> {
  return viewRequest("", boardId, "GET");
}

export function createBoardView(
  boardId: string, view: CardViewState & { name: string; shared: boolean },
): Promise<{ view: BoardView }> {
  return viewRequest("", boardId, "POST", view);
}

export function updateBoardView(
  boardId: string, viewId: string, updates: Partial<CardViewState & { name: string; shared: boolean }>,
): Promise<{ view: BoardView }> {
  return viewRequest(`/${encodeURIComponent(viewId)}`, boardId, "PATCH", updates);
}

export function deleteBoardView(boardId: string, viewId: string): Promise<{ deleted: boolean }> {
  return viewRequest(`/${encodeURIComponent(viewId)}`, boardId, "DELETE");
}

// ---------- Dictation (speech-to-text) ----------

export async function transcribeAudio(blob: Blob, filename: string): Promise<string> {
  const form = new FormData();
  form.append("audio", blob, filename);
  const res = await fetch(`${API_BASE}/api/transcribe`, { method: "POST", body: form });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Transcription failed: ${res.status}`);
  return body.text || "";
}

// ---------- Personal AI usage ----------

/** Sums shared by totals, days, models and chats on /api/usage/me. */
export interface MyUsageSums {
  total_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  /** 0 on runs recorded before cache capture */
  cache_read_tokens: number;
  cache_creation_tokens: number;
  runs: number;
  /** Runs whose runtime reports no price (e.g. Codex on a ChatGPT plan) */
  unpriced_runs: number;
  unpriced_tokens: number;
}

export interface MyUsageDay extends MyUsageSums {
  date: string;
  conversations: number;
}

export interface MyUsageModel extends MyUsageSums {
  /** provider/model id, or "unknown" */
  model: string;
  conversations: number;
}

export interface MyUsageChat extends MyUsageSums {
  conversation_id: string;
  title?: string | null;
  prompt: string;
  started_at?: string | null;
  last_used_at?: string | null;
  status?: string | null;
  deleted?: boolean;
  models: string[];
  /** Includes spend recorded before per-run tracking */
  approx?: boolean;
}

/** @deprecated use MyUsageChat */
export type MyUsageTopChat = MyUsageChat;

export type MyUsageSort = "cost_desc" | "cost_asc" | "recent";

export interface MyUsageResponse {
  days: number | null;
  since: string;
  basis?: string;
  includes_approximate?: boolean;
  totals: MyUsageSums & { conversations: number };
  daily: MyUsageDay[];
  by_model: MyUsageModel[];
  chats: MyUsageChat[];
  chats_total: number;
  chats_sort: MyUsageSort;
  chats_limit: number;
  chats_offset: number;
  top_chats: MyUsageChat[];
}

export async function fetchMyUsage(opts: {
  /** Rolling window in days (ignored when `since` is set) */
  days?: number;
  /** Exact window start (ISO) — e.g. local midnight for "today" */
  since?: string;
  /** IANA timezone so daily buckets match the user's local days */
  tz?: string;
  sort?: MyUsageSort;
  limit?: number;
  offset?: number;
}): Promise<MyUsageResponse> {
  const params = new URLSearchParams();
  if (opts.since) params.set("since", opts.since);
  else if (opts.days) params.set("days", String(opts.days));
  if (opts.tz) params.set("tz", opts.tz);
  if (opts.sort) params.set("sort", opts.sort);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  const res = await fetch(`${API_BASE}/api/usage/me?${params}`);
  if (!res.ok) throw new Error(`Failed to fetch usage: ${res.status}`);
  return res.json();
}

export interface MyUsageRun {
  at: string;
  model: string;
  runtime: string | null;
  source?: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cost_usd: number;
  cost_known: boolean;
  approx: boolean;
}

export async function fetchMyChatRuns(
  conversationId: string, since: string,
): Promise<{ runs: MyUsageRun[]; truncated: boolean }> {
  const params = new URLSearchParams({ since });
  const res = await fetch(
    `${API_BASE}/api/usage/me/chats/${encodeURIComponent(conversationId)}/runs?${params}`,
  );
  if (!res.ok) throw new Error(`Failed to fetch runs: ${res.status}`);
  return res.json();
}

export interface ConversationCost {
  total_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  /** 0 on conversations recorded before cache capture */
  cache_read_tokens: number;
  cache_creation_tokens: number;
  total_turns: number;
  status: string | null;
}

export async function fetchConversationCost(conversationId: string): Promise<ConversationCost> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/cost`);
  if (!res.ok) throw new Error(`Failed to fetch cost: ${res.status}`);
  return res.json();
}

export interface GoogleSkillSource {
  type: "google_doc" | "google_sheet";
  spreadsheet_id?: string;
  sheet_id?: number;
  sync_mode?: "pull_only";
  header_row?: boolean;
  document_id?: string;
  tab_id: string;
  tab_title: string;
  title: string;
  connection_owner: string;
  auto_sync_enabled: boolean;
  status: string;
  hash: string;
  last_checked?: string;
  last_published?: string;
  error?: string;
}

export interface GoogleSkillPreview {
  sync_mode?: "pull_only";
  disclosure?: string;
  document_id?: string;
  title: string;
  tabs: { id: string; title: string }[];
  can_edit: boolean;
  connection_owner: string;
  tab_id?: string;
  content?: string;
  hash?: string;
}

export async function skillSourceRequest<T>(path: string, body?: object): Promise<T> {
  const res = await fetch(`${API_BASE}/api/${path}`, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Google skill request failed");
  return data;
}
