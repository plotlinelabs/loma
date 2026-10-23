"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  RiAddLine,
  RiDeleteBinLine,
  RiLoader4Line,
  RiRobot2Line,
} from "@remixicon/react";
import { AgentAvatar } from "@/components/AgentAvatar";
import { fetchAgentIdentities, type AgentIdentity } from "../../../../lib/agents-api";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getToolMeta, CATEGORIES } from "../../../mcp/tool-meta";
import { TOOL_LOGOS } from "../../../mcp/tool-logos";
import {
  deleteTeam,
  fetchTeam,
  fetchToolConfigs,
  fetchUsers,
  updateTeam,
  type User,
  type Team,
  type TeamAgentSummary,
  type ToolConfig,
} from "../../../../lib/governance-api";

/* ── Helpers ──────────────────────────────────────────────────────── */

const ALL_TOOLS = CATEGORIES.flatMap((c) => c.keys);

/* ── Page ─────────────────────────────────────────────────────────── */

export default function TeamDetailPage() {
  const params = useParams();
  const router = useRouter();
  const teamId = params.id as string;
  const [team, setTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<User[]>([]);
  const [agents, setAgents] = useState<TeamAgentSummary[]>([]);
  const [allUsers, setAllUsers] = useState<User[] | null>(null);
  const [allAgents, setAllAgents] = useState<AgentIdentity[]>([]);
  const [memberToAdd, setMemberToAdd] = useState("");
  const [agentToAdd, setAgentToAdd] = useState("");
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [toolConfigMap, setToolConfigMap] = useState<Record<string, ToolConfig>>({});
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  async function reloadTeam() {
    const teamData = await fetchTeam(teamId);
    setTeam(teamData.team);
    setMembers(teamData.members);
    setAgents(teamData.agents || []);
  }

  async function saveTeam(updates: { members?: string[]; agents?: string[] }) {
    setSaving(true);
    setActionError(null);
    try {
      await updateTeam(teamId, updates);
      await reloadTeam();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Failed to update team");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteTeam() {
    if (!team || !confirm(`Delete team "${team.name}"? Its skills and flows will move to "No team".`)) return;
    try {
      await deleteTeam(teamId);
      router.push("/admin");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Failed to delete team");
    }
  }

  useEffect(() => {
    fetchUsers().then(setAllUsers).catch(() => setAllUsers(null));
    fetchAgentIdentities().then((d) => setAllAgents(d.agents)).catch(() => setAllAgents([]));
  }, []);

  useEffect(() => {
    Promise.all([fetchTeam(teamId), fetchToolConfigs()])
      .then(([teamData, tc]) => {
        setTeam(teamData.team);
        setMembers(teamData.members);
        setAgents(teamData.agents || []);
        const map: Record<string, ToolConfig> = {};
        for (const c of tc) map[c.tool_key] = c;
        setToolConfigMap(map);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [teamId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (notFound || !team) {
    return (
      <div className="space-y-4 animate-fade-in-up">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink href="/admin">Admin</BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbLink href="/admin">Teams</BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>Not found</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        <div className="bg-card rounded-xl border border-border p-6 text-center">
          <p className="text-muted-foreground">Team not found</p>
        </div>
      </div>
    );
  }

  const teamAgents = team.agents || [];
  const addableUsers = (allUsers || []).filter((u) => !team.members.includes(u.email));
  const addableAgents = allAgents.filter((a) => !teamAgents.includes(a.agent_id));

  function addMember() {
    const email = memberToAdd.trim();
    if (!email || !team) return;
    setMemberToAdd("");
    saveTeam({ members: [...team.members, email] });
  }

  function addAgent() {
    if (!agentToAdd || !team) return;
    setAgentToAdd("");
    saveTeam({ agents: [...teamAgents, agentToAdd] });
  }

  const lomaTools = ALL_TOOLS.filter((t) => toolConfigMap[t]?.auth_mode === "loma-managed");
  const oauthTools = ALL_TOOLS.filter((t) => toolConfigMap[t]?.auth_mode === "tool-managed");

  return (
    <div className="space-y-3 animate-fade-in-up">
      {/* Breadcrumb */}
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="/admin">Admin</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbLink href="/admin">Teams</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{team.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* Team Header */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3">
        <div className="flex items-center gap-3">
          <div
            className="w-14 h-14 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ backgroundColor: team.bg_color }}
          >
            <span className="text-2xl font-bold" style={{ color: team.color }}>
              {team.name.charAt(0)}
            </span>
          </div>
          <div className="flex-1">
            <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">{team.name}</h1>
            <div className="flex items-center gap-2 mt-1">
              <Badge
                variant="secondary"
                className="text-[10px]"
                style={{ backgroundColor: team.bg_color, color: team.color }}
              >
                {team.members.length} members
              </Badge>
              <Badge variant="secondary" className="text-[10px] bg-muted text-muted-foreground">
                {teamAgents.length} agents
              </Badge>
              <Badge variant="secondary" className="text-[10px] bg-muted text-muted-foreground">
                {Object.keys(team.tool_defaults).length} tool defaults
              </Badge>
            </div>
          </div>
          <Button
            variant="ghost"
            size="xs"
            onClick={handleDeleteTeam}
            className="text-muted-foreground hover:text-red-600"
            data-testid="delete-team-button"
          >
            <RiDeleteBinLine size={14} />
            Delete team
          </Button>
        </div>
      </div>

      {actionError && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{actionError}</div>
      )}

      {/* Members */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-[13px] font-heading font-semibold text-foreground">Members</h2>
          <div className="flex items-center gap-1.5">
            {allUsers ? (
              <Select value={memberToAdd} onValueChange={setMemberToAdd}>
                <SelectTrigger className="text-[12px] h-7 w-56" data-testid="team-add-member-select">
                  <SelectValue placeholder={addableUsers.length ? "Choose a person…" : "Everyone is in this team"} />
                </SelectTrigger>
                <SelectContent>
                  {addableUsers.map((u) => (
                    <SelectItem key={u.email} value={u.email}>{u.name || u.email} · {u.email}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <input
                value={memberToAdd}
                onChange={(e) => setMemberToAdd(e.target.value)}
                placeholder="name@company.com"
                className="h-7 w-56 rounded-md border border-border bg-background px-2 text-[12px]"
                data-testid="team-add-member-input"
              />
            )}
            <Button
              variant="ghost"
              size="xs"
              className="text-brand-600 hover:text-brand-700"
              disabled={!memberToAdd || saving}
              onClick={addMember}
              data-testid="team-add-member"
            >
              <RiAddLine size={14} />
              Add Member
            </Button>
          </div>
        </div>
        {members.length === 0 && <p className="text-xs text-muted-foreground py-2">No members yet.</p>}

        <div className="divide-y divide-border/50">
          {members.map((user) => (
            <div key={user.email} className="flex items-center gap-2 py-3 first:pt-0 last:pb-0">
              <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                <span className="text-[13px] font-medium text-brand-700">{user.avatar}</span>
              </div>
              <div className="flex-1 min-w-0">
                <Link
                  href={`/admin/${encodeURIComponent(user.email)}`}
                  className="text-[13px] font-medium text-foreground hover:text-brand-600 transition-colors"
                >
                  {user.name}
                </Link>
                <div className="text-xs text-muted-foreground">{user.email}</div>
              </div>
              <Button
                variant="ghost"
                size="xs"
                className="text-[10px] text-muted-foreground hover:text-red-500"
                disabled={saving}
                onClick={() => saveTeam({ members: team.members.filter((m) => m !== user.email) })}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      </div>

      {/* Agents */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3" data-testid="team-agents">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-[13px] font-heading font-semibold text-foreground">Agents</h2>
            <p className="text-xs text-muted-foreground">Agents this team owns and works with.</p>
          </div>
          <div className="flex items-center gap-1.5">
            <Select value={agentToAdd} onValueChange={setAgentToAdd}>
              <SelectTrigger className="text-[12px] h-7 w-56" data-testid="team-add-agent-select">
                <SelectValue placeholder={addableAgents.length ? "Choose an agent…" : "No more agents to add"} />
              </SelectTrigger>
              <SelectContent>
                {addableAgents.map((a) => (
                  <SelectItem key={a.agent_id} value={a.agent_id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="xs"
              className="text-brand-600 hover:text-brand-700"
              disabled={!agentToAdd || saving}
              onClick={addAgent}
              data-testid="team-add-agent"
            >
              <RiAddLine size={14} />
              Add Agent
            </Button>
          </div>
        </div>
        {agents.length === 0 ? (
          <p className="text-xs text-muted-foreground py-2 flex items-center gap-1.5">
            <RiRobot2Line size={14} /> No agents in this team yet.
          </p>
        ) : (
          <div className="divide-y divide-border/50">
            {agents.map((agent) => (
              <div key={agent.agent_id} className="flex items-center gap-2 py-3 first:pt-0 last:pb-0">
                <AgentAvatar avatar={agent.avatar} size={32} className="rounded-full flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium text-foreground truncate">{agent.name}</div>
                  {agent.description && <div className="text-xs text-muted-foreground truncate">{agent.description}</div>}
                </div>
                <Button
                  variant="ghost"
                  size="xs"
                  className="text-[10px] text-muted-foreground hover:text-red-500"
                  disabled={saving}
                  onClick={() => saveTeam({ agents: teamAgents.filter((id) => id !== agent.agent_id) })}
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Tool Defaults — Loma-managed */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3">
        <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">Tool Defaults — Loma-managed</h2>
        <p className="text-xs text-muted-foreground mb-2">
          Default roles assigned to all team members. Individual users can override these.
        </p>

        <Table>
          <TableHeader>
            <TableRow className="">
              <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Tool</TableHead>
              <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Default Role</TableHead>
              <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Members Affected</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {lomaTools.map((toolKey) => {
              const meta = getToolMeta(toolKey);
              const Logo = TOOL_LOGOS[toolKey];
              const toolConfig = toolConfigMap[toolKey];
              const td = team.tool_defaults[toolKey];
              const defaultRole = td?.role ?? null;

              return (
                <TableRow key={toolKey} className="">
                  <TableCell className="pr-4">
                    <Link href={`/mcp/${toolKey}`} className="flex items-center gap-2.5 group">
                      <div
                        className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
                        style={{ backgroundColor: meta.bgColor }}
                      >
                        {Logo ? (
                          <Logo className="w-3.5 h-3.5" />
                        ) : (
                          <span className="text-[9px] font-bold" style={{ color: meta.color }}>
                            {meta.displayName.charAt(0)}
                          </span>
                        )}
                      </div>
                      <span className="text-[13px] font-medium text-foreground group-hover:text-brand-600 transition-colors">
                        {meta.displayName}
                      </span>
                    </Link>
                  </TableCell>
                  <TableCell className="pr-4">
                    {defaultRole ? (
                      <Select defaultValue={defaultRole}>
                        <SelectTrigger className="text-[13px] h-7 w-auto">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {toolConfig?.roles.map((r) => (
                            <SelectItem key={r.name} value={r.name}>{r.name}</SelectItem>
                          ))}
                          <SelectItem value="">No access</SelectItem>
                        </SelectContent>
                      </Select>
                    ) : (
                      <span className="text-xs text-muted-foreground">No default</span>
                    )}
                  </TableCell>
                  <TableCell className="pr-4">
                    <span className="text-[13px] text-muted-foreground">{team.members.length}</span>
                  </TableCell>
                  <TableCell className="text-right">
                    {defaultRole && (
                      <Button
                        variant="ghost"
                        size="xs"
                        className="text-xs text-muted-foreground hover:text-red-500"
                      >
                        Remove
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Tool Defaults — OAuth */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3">
        <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">Tool Defaults — OAuth</h2>
        <p className="text-xs text-muted-foreground mb-2">
          Whether team members are required to connect their accounts via OAuth.
        </p>

        <Table>
          <TableHeader>
            <TableRow className="">
              <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Tool</TableHead>
              <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">OAuth Required</TableHead>
              <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Connected</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {oauthTools.map((toolKey) => {
              const meta = getToolMeta(toolKey);
              const Logo = TOOL_LOGOS[toolKey];
              const td = team.tool_defaults[toolKey];
              const oauthRequired = td?.oauth_required ?? false;

              // Count how many team members have connected
              const connectedCount = members.filter((m) =>
                m.tool_assignments?.[toolKey]?.oauth_status === "connected"
              ).length;

              return (
                <TableRow key={toolKey} className="">
                  <TableCell className="pr-4">
                    <Link href={`/mcp/${toolKey}`} className="flex items-center gap-2.5 group">
                      <div
                        className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
                        style={{ backgroundColor: meta.bgColor }}
                      >
                        {Logo ? (
                          <Logo className="w-3.5 h-3.5" />
                        ) : (
                          <span className="text-[9px] font-bold" style={{ color: meta.color }}>
                            {meta.displayName.charAt(0)}
                          </span>
                        )}
                      </div>
                      <span className="text-[13px] font-medium text-foreground group-hover:text-brand-600 transition-colors">
                        {meta.displayName}
                      </span>
                    </Link>
                  </TableCell>
                  <TableCell className="pr-4">
                    {oauthRequired ? (
                      <Badge variant="secondary" className="text-[10px] bg-blue-50 text-blue-600">
                        Required
                      </Badge>
                    ) : (
                      <Badge variant="secondary" className="text-[10px] bg-muted text-muted-foreground">
                        Optional
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="pr-4">
                    <span className={`text-[13px] font-medium ${
                      connectedCount === team.members.length ? "text-emerald-600" : "text-muted-foreground"
                    }`}>
                      {connectedCount}/{team.members.length}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
