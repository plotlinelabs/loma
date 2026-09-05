"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useUser } from "@/lib/UserContext";
import { fetchSkills, basePath, type Skill } from "@/lib/api";
import { fetchIntegrations, type Integration } from "@/lib/integration-api";
import {
  createAgentIdentity,
  deleteAgentIdentity,
  fetchAgentIdentities,
  updateAgentIdentity,
  type AgentIdentity,
  type AgentIdentityInput,
  type AgentMotif,
} from "@/lib/agents-api";
import { AgentAvatar, randomAvatarSpec } from "@/components/AgentAvatar";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { EmptyState } from "@/components/EmptyState";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  RiAddLine,
  RiChat1Line,
  RiDeleteBinLine,
  RiPencilLine,
  RiRefreshLine,
  RiRobot2Line,
} from "@remixicon/react";

const MOTIFS: AgentMotif[] = ["round", "square", "halo", "antenna"];

// Personal CLI tools every deployment ships with (tools/*.py); org integrations
// are appended from the connected-integrations list at runtime.
const PERSONAL_TOOLS = [
  "gmail",
  "google-drive",
  "google-calendar",
  "google-docs",
  "google-sheets",
  "slack",
  "telegram",
];

interface EditorState {
  agent: AgentIdentity | null; // null = creating
  input: Required<Pick<AgentIdentityInput, "name" | "description" | "identity_prompt" | "skills" | "tools" | "visibility" | "avatar">>;
}

function emptyEditor(): EditorState {
  return {
    agent: null,
    input: {
      name: "",
      description: "",
      identity_prompt: "",
      skills: [],
      tools: [],
      visibility: "private",
      avatar: randomAvatarSpec(),
    },
  };
}

function editorFor(agent: AgentIdentity): EditorState {
  return {
    agent,
    input: {
      name: agent.name,
      description: agent.description,
      identity_prompt: agent.identity_prompt || "",
      skills: agent.skills || [],
      tools: agent.tools || [],
      visibility: agent.visibility,
      avatar: agent.avatar || randomAvatarSpec(),
    },
  };
}

function ChipToggle({
  label,
  active,
  onToggle,
  title,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      title={title}
      className={cn(
        "rounded-full px-2.5 py-1 text-xs transition-colors",
        active
          ? "bg-brand-800 text-brand-50 dark:bg-brand-200 dark:text-brand-900"
          : "bg-muted text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

export default function AgentsPage() {
  const router = useRouter();
  const { user, hasRole } = useUser();
  const [agents, setAgents] = useState<AgentIdentity[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [saving, setSaving] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const loadAgents = useCallback(async () => {
    try {
      const data = await fetchAgentIdentities();
      setAgents(data.agents || []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAgents();
    fetchSkills()
      .then((data) => setSkills(data.skills || []))
      .catch(() => {
        setSkills([]);
        setSkillsError("Skills could not be loaded. Reload the page to try again.");
      });
    fetchIntegrations()
      .then((list) => setIntegrations(list.filter((i) => i.status === "connected")))
      .catch(() => setIntegrations([]));
  }, [loadAgents]);

  const toolOptions = useMemo(() => {
    const fromIntegrations = integrations.map((i) => i.display_name || i.provider);
    return [...new Set([...fromIntegrations, ...PERSONAL_TOOLS])];
  }, [integrations]);

  const canManage = useCallback(
    (agent: AgentIdentity) =>
      user?.email === agent.created_by || user?.system_role === "admin",
    [user],
  );

  const openEditor = (state: EditorState) => {
    setEditor(state);
    setEditorError(null);
    setConfirmingDelete(false);
  };

  const updateInput = (patch: Partial<EditorState["input"]>) => {
    setEditor((prev) => (prev ? { ...prev, input: { ...prev.input, ...patch } } : prev));
  };

  const handleSave = async () => {
    if (!editor) return;
    setSaving(true);
    setEditorError(null);
    try {
      if (editor.agent) {
        await updateAgentIdentity(editor.agent.agent_id, editor.input);
      } else {
        await createAgentIdentity(editor.input);
      }
      setEditor(null);
      await loadAgents();
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : "Failed to save agent");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!editor?.agent) return;
    setSaving(true);
    try {
      await deleteAgentIdentity(editor.agent.agent_id);
      setEditor(null);
      await loadAgents();
    } catch (e) {
      setEditorError(e instanceof Error ? e.message : "Failed to delete agent");
    } finally {
      setSaving(false);
    }
  };

  const startChat = (agent: AgentIdentity) => {
    try {
      window.localStorage.setItem("dashboard-chat-selected-agent", agent.agent_id);
    } catch {}
    router.push(`${basePath}/chat`);
  };

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="px-3 lg:px-4 py-4 border-b border-border bg-card flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Agents</h1>
          <p className="text-[13px] text-muted-foreground">
            Create and share agents with their own persona, skills, and tool scope.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => openEditor(emptyEditor())}
          className="bg-accent-200 hover:bg-accent-300 text-accent-on shrink-0"
        >
          <RiAddLine size={16} />
          New agent
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-4">
        {error && (
          <Alert variant="destructive" className="mb-3">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {loading ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-32 rounded-xl" />
            ))}
          </div>
        ) : agents.length === 0 ? (
          <EmptyState
            icon={RiRobot2Line}
            title="No agents yet"
            description="An agent is a shareable persona with its own skills and tool scope — like a specialist teammate. Create the first one."
            action="New agent"
            onAction={() => openEditor(emptyEditor())}
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <Card key={agent.agent_id} className="p-4 flex flex-col gap-3">
                <div className="flex items-start gap-3">
                  <AgentAvatar avatar={agent.avatar} size={44} />
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] font-medium text-foreground truncate">{agent.name}</p>
                    <p className="text-xs text-muted-foreground line-clamp-2">{agent.description}</p>
                  </div>
                </div>
                <div className="mt-auto flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        agent.visibility === "workspace" ? "bg-emerald-500" : "bg-gray-400",
                      )}
                    />
                    {agent.visibility === "workspace" ? "Shared" : "Private"}
                  </span>
                  <span className="truncate">
                    {agent.created_by === user?.email ? "by you" : `by ${agent.created_by}`}
                  </span>
                  {(agent.conversation_count ?? 0) > 0 && (
                    <span className="shrink-0">{agent.conversation_count} chats</span>
                  )}
                  <span className="ml-auto flex items-center gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      title={`Chat with ${agent.name}`}
                      onClick={() => startChat(agent)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <RiChat1Line size={14} />
                    </Button>
                    {canManage(agent) && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        title="Edit agent"
                        onClick={() => openEditor(editorFor(agent))}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <RiPencilLine size={14} />
                      </Button>
                    )}
                  </span>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Dialog open={!!editor} onOpenChange={(open) => !open && setEditor(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editor?.agent ? "Edit agent" : "New agent"}</DialogTitle>
          </DialogHeader>

          {editor && (
            <div className="grid gap-4">
              <div className="flex items-center gap-3">
                <AgentAvatar avatar={editor.input.avatar} size={56} />
                <div className="grid gap-1.5">
                  <div className="flex items-center gap-1">
                    {MOTIFS.map((motif) => (
                      <button
                        key={motif}
                        type="button"
                        title={motif}
                        onClick={() =>
                          updateInput({ avatar: { ...editor.input.avatar, motif } })
                        }
                        className={cn(
                          "rounded-md p-0.5 transition-colors",
                          editor.input.avatar.motif === motif
                            ? "bg-muted ring-1 ring-border"
                            : "opacity-60 hover:opacity-100",
                        )}
                      >
                        <AgentAvatar avatar={{ seed: editor.input.avatar.seed, motif }} size={22} />
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      updateInput({ avatar: randomAvatarSpec(editor.input.avatar.motif) })
                    }
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  >
                    <RiRefreshLine size={12} />
                    Shuffle look
                  </button>
                </div>
              </div>

              <div className="grid gap-1.5">
                <Label htmlFor="agent-name">Name</Label>
                <Input
                  id="agent-name"
                  value={editor.input.name}
                  onChange={(e) => updateInput({ name: e.target.value })}
                  placeholder="Support Triager"
                  maxLength={60}
                />
              </div>

              <div className="grid gap-1.5">
                <Label htmlFor="agent-description">Description</Label>
                <Input
                  id="agent-description"
                  value={editor.input.description}
                  onChange={(e) => updateInput({ description: e.target.value })}
                  placeholder="Triages support tickets and drafts replies — 1–2 lines so others know what it does"
                  maxLength={200}
                />
              </div>

              <div className="grid gap-1.5">
                <Label htmlFor="agent-prompt">Instructions</Label>
                <Textarea
                  id="agent-prompt"
                  value={editor.input.identity_prompt}
                  onChange={(e) => updateInput({ identity_prompt: e.target.value })}
                  placeholder="How should this agent behave? Persona, priorities, playbook..."
                  rows={4}
                  className="text-[13px]"
                />
              </div>

              {skillsError && <p role="alert" className="text-sm text-destructive">{skillsError}</p>}
              {skills.length > 0 && (
                <div className="grid gap-1.5">
                  <Label>Skills</Label>
                  <p className="text-xs text-muted-foreground -mt-1">
                    Leave empty for all skills; pick some to focus the agent.
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {skills.map((skill) => {
                      const slug = skill.slug || skill.name;
                      const active = editor.input.skills.includes(slug);
                      return (
                        <ChipToggle
                          key={slug}
                          label={skill.name}
                          title={skill.description}
                          active={active}
                          onToggle={() =>
                            updateInput({
                              skills: active
                                ? editor.input.skills.filter((s) => s !== slug)
                                : [...editor.input.skills, slug],
                            })
                          }
                        />
                      );
                    })}
                  </div>
                </div>
              )}

              {toolOptions.length > 0 && (
                <div className="grid gap-1.5">
                  <Label>Tools</Label>
                  <p className="text-xs text-muted-foreground -mt-1">
                    Leave empty for all tools; pick some to scope what the agent may use.
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {toolOptions.map((tool) => {
                      const active = editor.input.tools.includes(tool);
                      return (
                        <ChipToggle
                          key={tool}
                          label={tool}
                          active={active}
                          onToggle={() =>
                            updateInput({
                              tools: active
                                ? editor.input.tools.filter((t) => t !== tool)
                                : [...editor.input.tools, tool],
                            })
                          }
                        />
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="grid gap-1.5">
                <Label>Sharing</Label>
                <div className="flex items-center gap-1.5">
                  <ChipToggle
                    label="Private"
                    active={editor.input.visibility === "private"}
                    onToggle={() => updateInput({ visibility: "private" })}
                  />
                  <ChipToggle
                    label="Everyone in workspace"
                    active={editor.input.visibility === "workspace"}
                    onToggle={() => updateInput({ visibility: "workspace" })}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  Agents act with the credentials of whoever is chatting with them.
                  {editor.input.visibility === "workspace" && !hasRole("operator") &&
                    " Sharing with the workspace requires operator access."}
                </p>
              </div>

              {editorError && (
                <Alert variant="destructive">
                  <AlertDescription>{editorError}</AlertDescription>
                </Alert>
              )}

              <div className="flex items-center justify-between gap-2">
                {editor.agent ? (
                  confirmingDelete ? (
                    <span className="flex items-center gap-1.5">
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={handleDelete}
                        disabled={saving}
                        className="bg-red-600 hover:bg-red-700 text-white"
                      >
                        Delete
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(false)}>
                        Cancel
                      </Button>
                    </span>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setConfirmingDelete(true)}
                      className="text-muted-foreground hover:text-red-600"
                    >
                      <RiDeleteBinLine size={14} />
                      Delete
                    </Button>
                  )
                ) : (
                  <span />
                )}
                <span className="flex items-center gap-1.5">
                  <Button variant="secondary" size="sm" onClick={() => setEditor(null)} disabled={saving}>
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleSave}
                    disabled={saving || !editor.input.name.trim() || !editor.input.description.trim()}
                    className="bg-accent-200 hover:bg-accent-300 text-accent-on"
                  >
                    {saving ? "Saving..." : editor.agent ? "Save changes" : "Create agent"}
                  </Button>
                </span>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
