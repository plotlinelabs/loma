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
  fetchShareDirectory,
  updateAgentIdentity,
  type AgentIdentity,
  type AgentIdentityInput,
  type AgentMotif,
  type AgentSharedWith,
  type DirectoryTeam,
  type DirectoryUser,
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
  RiCloseLine,
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

type ShareMode = "private" | "specific" | "workspace";

interface EditorState {
  agent: AgentIdentity | null; // null = creating
  shareMode: ShareMode;
  input: Required<Pick<AgentIdentityInput, "name" | "description" | "identity_prompt" | "skills" | "tools" | "visibility" | "avatar">> & {
    shared_with: AgentSharedWith;
  };
}

function emptyEditor(): EditorState {
  return {
    agent: null,
    shareMode: "private",
    input: {
      name: "",
      description: "",
      identity_prompt: "",
      skills: [],
      tools: [],
      visibility: "private",
      shared_with: { users: [], teams: [] },
      avatar: randomAvatarSpec(),
    },
  };
}

function editorFor(agent: AgentIdentity): EditorState {
  const shared = agent.shared_with || { users: [], teams: [] };
  return {
    agent,
    shareMode:
      agent.visibility === "workspace"
        ? "workspace"
        : shared.users.length || shared.teams.length
          ? "specific"
          : "private",
    input: {
      name: agent.name,
      description: agent.description,
      identity_prompt: agent.identity_prompt || "",
      skills: agent.skills || [],
      tools: agent.tools || [],
      visibility: agent.visibility,
      shared_with: { users: shared.users || [], teams: shared.teams || [] },
      avatar: agent.avatar || randomAvatarSpec(),
    },
  };
}

function ChipToggle({
  label,
  active,
  onToggle,
  title,
  dotColor,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
  title?: string;
  dotColor?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition-colors",
        active
          ? "bg-brand-800 text-brand-50 dark:bg-brand-200 dark:text-brand-900"
          : "bg-muted text-muted-foreground hover:text-foreground",
      )}
    >
      {dotColor && <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: dotColor }} />}
      {label}
    </button>
  );
}

function GroupHeading({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
      {children}
    </p>
  );
}

/** Skills grouped scope-first (workspace vs personal), then by folder — the
 *  same hierarchy the Skills page uses, so nothing is a flat chip wall. */
function groupSkillsByFolder(list: Skill[]) {
  const map = new Map<string, Skill[]>();
  for (const skill of list) {
    const key = skill.folder || "Ungrouped";
    const group = map.get(key);
    if (group) group.push(skill);
    else map.set(key, [skill]);
  }
  return [...map.entries()]
    .sort(([a], [b]) => (a === "Ungrouped" ? 1 : b === "Ungrouped" ? -1 : a.localeCompare(b)))
    .map(([folder, items]) => ({
      folder,
      items: [...items].sort((a, b) => a.name.localeCompare(b.name)),
    }));
}

export default function AgentsPage() {
  const router = useRouter();
  const { user, hasRole } = useUser();
  const [agents, setAgents] = useState<AgentIdentity[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [directoryUsers, setDirectoryUsers] = useState<DirectoryUser[]>([]);
  const [directoryTeams, setDirectoryTeams] = useState<DirectoryTeam[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [saving, setSaving] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  const [peopleSearch, setPeopleSearch] = useState("");

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
    fetchShareDirectory()
      .then((data) => {
        setDirectoryUsers(data.users || []);
        setDirectoryTeams(data.teams || []);
      })
      .catch(() => {
        setDirectoryUsers([]);
        setDirectoryTeams([]);
      });
  }, [loadAgents]);

  const integrationTools = useMemo(
    () => [...new Set(integrations.map((i) => i.display_name || i.provider))],
    [integrations],
  );

  const skillGroups = useMemo(() => {
    const query = skillSearch.trim().toLowerCase();
    const visible = query
      ? skills.filter((s) =>
          `${s.name} ${s.slug || ""} ${s.description}`.toLowerCase().includes(query),
        )
      : skills;
    return {
      workspace: groupSkillsByFolder(visible.filter((s) => s.scope !== "personal")),
      personal: groupSkillsByFolder(visible.filter((s) => s.scope === "personal")),
    };
  }, [skills, skillSearch]);

  const canManage = useCallback(
    (agent: AgentIdentity) =>
      user?.email === agent.created_by || user?.system_role === "admin",
    [user],
  );

  const openEditor = (state: EditorState) => {
    setEditor(state);
    setEditorError(null);
    setConfirmingDelete(false);
    setSkillSearch("");
    setPeopleSearch("");
  };

  const updateInput = (patch: Partial<EditorState["input"]>) => {
    setEditor((prev) => (prev ? { ...prev, input: { ...prev.input, ...patch } } : prev));
  };

  const setShareMode = (shareMode: ShareMode) => {
    setEditor((prev) => (prev ? { ...prev, shareMode } : prev));
  };

  const toggleSkill = (slug: string) => {
    if (!editor) return;
    const active = editor.input.skills.includes(slug);
    updateInput({
      skills: active
        ? editor.input.skills.filter((s) => s !== slug)
        : [...editor.input.skills, slug],
    });
  };

  const toggleTool = (tool: string) => {
    if (!editor) return;
    const active = editor.input.tools.includes(tool);
    updateInput({
      tools: active
        ? editor.input.tools.filter((t) => t !== tool)
        : [...editor.input.tools, tool],
    });
  };

  const toggleShareTeam = (teamId: string) => {
    if (!editor) return;
    const { shared_with } = editor.input;
    const active = shared_with.teams.includes(teamId);
    updateInput({
      shared_with: {
        ...shared_with,
        teams: active
          ? shared_with.teams.filter((t) => t !== teamId)
          : [...shared_with.teams, teamId],
      },
    });
  };

  const toggleShareUser = (email: string) => {
    if (!editor) return;
    const { shared_with } = editor.input;
    const active = shared_with.users.includes(email);
    updateInput({
      shared_with: {
        ...shared_with,
        users: active
          ? shared_with.users.filter((u) => u !== email)
          : [...shared_with.users, email],
      },
    });
    setPeopleSearch("");
  };

  const handleSave = async () => {
    if (!editor) return;
    setSaving(true);
    setEditorError(null);
    try {
      const payload: AgentIdentityInput = {
        ...editor.input,
        visibility: editor.shareMode === "workspace" ? "workspace" : "private",
        shared_with:
          editor.shareMode === "specific"
            ? editor.input.shared_with
            : { users: [], teams: [] },
      };
      if (editor.agent) {
        await updateAgentIdentity(editor.agent.agent_id, payload);
      } else {
        await createAgentIdentity(payload);
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

  const shareLabel = (agent: AgentIdentity) => {
    if (agent.visibility === "workspace") return { label: "Everyone", dot: "bg-emerald-500" };
    const shared = agent.shared_with;
    const count = (shared?.users.length || 0) + (shared?.teams.length || 0);
    if (count > 0) return { label: `Shared with ${count}`, dot: "bg-amber-500" };
    return { label: "Private", dot: "bg-gray-400" };
  };

  const selectedPeople = editor?.input.shared_with.users || [];
  const peopleMatches = useMemo(() => {
    const query = peopleSearch.trim().toLowerCase();
    const unselected = directoryUsers.filter(
      (u) => !selectedPeople.includes(u.email) && u.email !== user?.email,
    );
    if (!query) return unselected.slice(0, directoryUsers.length <= 15 ? 15 : 0);
    return unselected
      .filter((u) => `${u.email} ${u.name}`.toLowerCase().includes(query))
      .slice(0, 8);
  }, [directoryUsers, selectedPeople, peopleSearch, user?.email]);

  const renderSkillGroups = (
    groups: { folder: string; items: Skill[] }[],
  ) =>
    groups.map((group) => (
      <div key={group.folder} className="grid gap-1.5">
        <GroupHeading>{group.folder}</GroupHeading>
        <div className="flex flex-wrap gap-1.5">
          {group.items.map((skill) => {
            const slug = skill.slug || skill.name;
            return (
              <ChipToggle
                key={slug}
                label={skill.name}
                title={skill.description}
                active={editor?.input.skills.includes(slug) || false}
                onToggle={() => toggleSkill(slug)}
              />
            );
          })}
        </div>
      </div>
    ));

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
            {agents.map((agent) => {
              const share = shareLabel(agent);
              return (
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
                      <span className={cn("h-1.5 w-1.5 rounded-full", share.dot)} />
                      {share.label}
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
              );
            })}
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
                <div className="grid gap-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <Label>Skills</Label>
                    <span className="text-xs text-muted-foreground">
                      {editor.input.skills.length > 0 ? (
                        <>
                          {editor.input.skills.length} selected
                          {" · "}
                          <button
                            type="button"
                            onClick={() => updateInput({ skills: [] })}
                            className="underline-offset-2 hover:underline"
                          >
                            clear
                          </button>
                        </>
                      ) : (
                        "empty = all skills"
                      )}
                    </span>
                  </div>
                  <Input
                    value={skillSearch}
                    onChange={(e) => setSkillSearch(e.target.value)}
                    placeholder="Search skills"
                    className="h-8 text-[13px]"
                  />
                  <div className="grid max-h-56 gap-2.5 overflow-y-auto rounded-lg bg-muted/40 p-2.5">
                    {skillGroups.workspace.length === 0 && skillGroups.personal.length === 0 && (
                      <p className="py-2 text-center text-xs text-muted-foreground">
                        No skills match that search.
                      </p>
                    )}
                    {renderSkillGroups(skillGroups.workspace)}
                    {skillGroups.personal.length > 0 && (
                      <div className="grid gap-2.5 border-t border-border pt-2.5">
                        <GroupHeading>Your personal skills</GroupHeading>
                        {renderSkillGroups(skillGroups.personal)}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {(integrationTools.length > 0 || PERSONAL_TOOLS.length > 0) && (
                <div className="grid gap-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <Label>Tools</Label>
                    <span className="text-xs text-muted-foreground">
                      {editor.input.tools.length > 0 ? `${editor.input.tools.length} selected` : "empty = all tools"}
                    </span>
                  </div>
                  <div className="grid gap-2.5 rounded-lg bg-muted/40 p-2.5">
                    {integrationTools.length > 0 && (
                      <div className="grid gap-1.5">
                        <GroupHeading>Integrations</GroupHeading>
                        <div className="flex flex-wrap gap-1.5">
                          {integrationTools.map((tool) => (
                            <ChipToggle
                              key={tool}
                              label={tool}
                              active={editor.input.tools.includes(tool)}
                              onToggle={() => toggleTool(tool)}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="grid gap-1.5">
                      <GroupHeading>Personal tools</GroupHeading>
                      <div className="flex flex-wrap gap-1.5">
                        {PERSONAL_TOOLS.map((tool) => (
                          <ChipToggle
                            key={tool}
                            label={tool}
                            active={editor.input.tools.includes(tool)}
                            onToggle={() => toggleTool(tool)}
                          />
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <div className="grid gap-2">
                <Label>Sharing</Label>
                <div className="flex flex-wrap items-center gap-1.5">
                  <ChipToggle
                    label="Private"
                    active={editor.shareMode === "private"}
                    onToggle={() => setShareMode("private")}
                  />
                  <ChipToggle
                    label="Specific people & teams"
                    active={editor.shareMode === "specific"}
                    onToggle={() => setShareMode("specific")}
                  />
                  <ChipToggle
                    label="Everyone in workspace"
                    active={editor.shareMode === "workspace"}
                    onToggle={() => setShareMode("workspace")}
                  />
                </div>

                {editor.shareMode === "specific" && (
                  <div className="grid gap-2.5 rounded-lg bg-muted/40 p-2.5">
                    {directoryTeams.length > 0 && (
                      <div className="grid gap-1.5">
                        <GroupHeading>Teams</GroupHeading>
                        <div className="flex flex-wrap gap-1.5">
                          {directoryTeams.map((team) => (
                            <ChipToggle
                              key={team.team_id}
                              label={team.name}
                              dotColor={team.color}
                              active={editor.input.shared_with.teams.includes(team.team_id)}
                              onToggle={() => toggleShareTeam(team.team_id)}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="grid gap-1.5">
                      <GroupHeading>People</GroupHeading>
                      {selectedPeople.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {selectedPeople.map((email) => {
                            const person = directoryUsers.find((u) => u.email === email);
                            return (
                              <button
                                key={email}
                                type="button"
                                onClick={() => toggleShareUser(email)}
                                title="Remove"
                                className="inline-flex items-center gap-1 rounded-full bg-brand-800 px-2.5 py-1 text-xs text-brand-50 dark:bg-brand-200 dark:text-brand-900"
                              >
                                {person?.name || email}
                                <RiCloseLine size={12} />
                              </button>
                            );
                          })}
                        </div>
                      )}
                      <Input
                        value={peopleSearch}
                        onChange={(e) => setPeopleSearch(e.target.value)}
                        placeholder={directoryUsers.length > 0 ? "Search people by name or email" : "No teammates found"}
                        disabled={directoryUsers.length === 0}
                        className="h-8 text-[13px]"
                      />
                      {peopleMatches.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {peopleMatches.map((person) => (
                            <ChipToggle
                              key={person.email}
                              label={person.name || person.email}
                              title={person.email}
                              active={false}
                              onToggle={() => toggleShareUser(person.email)}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <p className="text-xs text-muted-foreground">
                  Agents act with the credentials of whoever is chatting with them.
                  {editor.shareMode === "workspace" && !hasRole("operator") &&
                    " Sharing with the whole workspace requires operator access."}
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
