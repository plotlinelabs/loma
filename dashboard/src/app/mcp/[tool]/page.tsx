"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { getToolMeta, CATEGORIES } from "../tool-meta";
import { TOOL_LOGOS } from "../tool-logos";
import {
  fetchUsers,
  fetchTeams,
  fetchToolConfigs,
  getEffectiveRole,
  formatRelativeTime,
  type User,
  type Team,
  type ToolConfig,
} from "../../../lib/governance-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { RiAddLine, RiLoader4Line } from "@remixicon/react";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";

/* -- Helpers ---------------------------------------------------------------- */

function getAllToolKeys(): string[] {
  return CATEGORIES.flatMap((c) => c.keys);
}

/* -- Components ------------------------------------------------------------- */

function StatusBadge({ status }: { status: "connected" | "expired" | "not_connected" | null }) {
  if (status === "connected")
    return <Badge className="text-[10px] bg-emerald-50 text-emerald-600 border-transparent">Connected</Badge>;
  if (status === "expired")
    return <Badge className="text-[10px] bg-amber-50 text-amber-600 border-transparent">Expired</Badge>;
  return <Badge variant="secondary" className="text-[10px]">Not connected</Badge>;
}

function RoleRow({ role }: { role: { name: string; description: string } }) {
  return (
    <TableRow>
      <TableCell>
        <span className="text-sm font-medium text-foreground">{role.name}</span>
      </TableCell>
      <TableCell>
        <span className="text-sm text-muted-foreground">{role.description}</span>
      </TableCell>
      <TableCell className="text-right">
        <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-red-500">Remove</Button>
      </TableCell>
    </TableRow>
  );
}

/* -- Main Page -------------------------------------------------------------- */

export default function ToolDetailPage() {
  const params = useParams();
  const toolKey = params.tool as string;
  const meta = getToolMeta(toolKey);
  const Logo = TOOL_LOGOS[toolKey];
  const allKeys = getAllToolKeys();
  const isValidTool = allKeys.includes(toolKey);

  const [config, setConfig] = useState<ToolConfig | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [allTeams, setAllTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [authMode, setAuthMode] = useState<"loma-managed" | "tool-managed">("loma-managed");

  useEffect(() => {
    if (!isValidTool) {
      setLoading(false);
      return;
    }
    Promise.all([fetchToolConfigs(), fetchUsers(), fetchTeams()])
      .then(([tc, u, t]) => {
        const found = tc.find((c) => c.tool_key === toolKey);
        setConfig(found ?? null);
        setAuthMode(found?.auth_mode ?? "loma-managed");
        setUsers(u);
        setAllTeams(t);
      })
      .catch((e) => console.error("Failed to load tool data:", e))
      .finally(() => setLoading(false));
  }, [toolKey, isValidTool]);

  if (!isValidTool) {
    return (
      <div className="space-y-4 animate-fade-in-up">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink href="/admin">Admin</BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>Tool not found</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        <Card>
          <CardContent className="p-6 text-center">
            <p className="text-muted-foreground">Tool not found</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RiLoader4Line size={32} className="animate-spin text-foreground" />
      </div>
    );
  }

  // Users who have assignments for this tool
  const toolUsers = users
    .map((user) => ({
      ...user,
      assignment: user.tool_assignments?.[toolKey] ?? null,
    }))
    .filter((u) => u.assignment);

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
            <BreadcrumbPage>{meta.displayName}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* Tool Header */}
      <Card>
        <CardContent>
          <div className="flex items-center gap-3">
            <div
              className="w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0"
              style={{ backgroundColor: meta.bgColor }}
            >
              {Logo ? (
                <Logo className="w-6 h-6" />
              ) : (
                <span className="text-lg font-bold" style={{ color: meta.color }}>
                  {meta.displayName.charAt(0)}
                </span>
              )}
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">{meta.displayName}</h1>
              <div className="flex items-center gap-2 mt-1">
                <Badge className="text-[10px]" style={{ backgroundColor: meta.bgColor, color: meta.color }}>
                  {meta.authMethod}
                </Badge>
                {meta.supportsOAuth && (
                  <Badge className="text-[10px] bg-blue-50 text-blue-600 border-transparent">
                    OAuth supported
                  </Badge>
                )}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Auth Mode Toggle */}
      <Card>
        <CardContent>
          <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Authentication Mode</h2>
          <div className="inline-flex rounded-lg border border-border p-0.5 bg-muted">
            <Button
              variant={authMode === "loma-managed" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setAuthMode("loma-managed")}
              className={authMode === "loma-managed" ? "shadow-sm" : ""}
            >
              Loma-managed
            </Button>
            <Button
              variant={authMode === "tool-managed" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setAuthMode("tool-managed")}
              disabled={!meta.supportsOAuth}
              className={authMode === "tool-managed" ? "shadow-sm" : ""}
            >
              Tool-managed (OAuth)
            </Button>
          </div>
          {!meta.supportsOAuth && (
            <p className="text-xs text-muted-foreground mt-2">
              This tool does not support OAuth. Only Loma-managed authentication is available.
            </p>
          )}
        </CardContent>
      </Card>

      {/* -- Loma-managed mode -- */}
      {authMode === "loma-managed" && (
        <>
          {/* Roles */}
          <Card>
            <CardContent>
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-sm font-heading font-semibold text-foreground">Roles</h2>
                <Button variant="ghost" size="xs" className="text-brand-600 hover:text-brand-700">
                  <RiAddLine size={14} />
                  Add Role
                </Button>
              </div>

              {config?.roles && config.roles.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow className="border-b border-border">
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Role</TableHead>
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Description</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {config.roles.map((role) => (
                      <RoleRow key={role.name} role={role} />
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <div className="text-center py-6">
                  <p className="text-sm text-muted-foreground">No roles defined yet</p>
                  <p className="text-xs text-muted-foreground/60 mt-1">Add a role to start managing access</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Team Assignments */}
          {(() => {
            const teamsWithDefaults = allTeams.filter((t) => t.tool_defaults[toolKey]?.role);
            return teamsWithDefaults.length > 0 ? (
              <Card>
                <CardContent>
                  <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Team Assignments</h2>
                  <Table>
                    <TableHeader>
                      <TableRow className="border-b border-border">
                        <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Team</TableHead>
                        <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Default Role</TableHead>
                        <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Members</TableHead>
                        <TableHead />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {teamsWithDefaults.map((team) => (
                        <TableRow key={team.team_id}>
                          <TableCell>
                            <Link href={`/admin/teams/${team.team_id}`} className="flex items-center gap-2.5 group">
                              <div
                                className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
                                style={{ backgroundColor: team.bg_color }}
                              >
                                <span className="text-xs font-bold" style={{ color: team.color }}>
                                  {team.name.charAt(0)}
                                </span>
                              </div>
                              <span className="text-sm font-medium text-foreground group-hover:text-brand-600 transition-colors">
                                {team.name}
                              </span>
                            </Link>
                          </TableCell>
                          <TableCell>
                            <Badge className="text-[10px] bg-blue-50 text-blue-600 border-transparent">
                              {team.tool_defaults[toolKey]?.role}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1">
                              {team.members.slice(0, 4).map((memberEmail) => {
                                const member = users.find((u) => u.email === memberEmail);
                                return (
                                  <div
                                    key={memberEmail}
                                    className="w-5 h-5 rounded-full bg-brand-100 flex items-center justify-center border border-white -ml-1 first:ml-0"
                                    title={member?.name ?? memberEmail}
                                  >
                                    <span className="text-[8px] font-medium text-brand-700">{member?.avatar ?? "?"}</span>
                                  </div>
                                );
                              })}
                              {team.members.length > 4 && (
                                <span className="text-[10px] text-muted-foreground ml-0.5">+{team.members.length - 4}</span>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="text-right">
                            <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-red-500">Remove</Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            ) : null;
          })()}

          {/* User Assignments */}
          <Card>
            <CardContent>
              <h2 className="text-sm font-heading font-semibold text-foreground mb-2">User Assignments</h2>

              {toolUsers.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow className="border-b border-border">
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">User</TableHead>
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Effective Role</TableHead>
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Source</TableHead>
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Last Used</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {toolUsers.map(({ email, name, avatar, assignment }) => {
                      const user = users.find((u) => u.email === email)!;
                      const effective = getEffectiveRole(user, allTeams, toolKey);
                      return (
                        <TableRow key={email}>
                          <TableCell>
                            <Link href={`/admin/${encodeURIComponent(email)}`} className="flex items-center gap-2.5 group">
                              <div className="w-7 h-7 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                                <span className="text-xs font-medium text-brand-700">{avatar}</span>
                              </div>
                              <div>
                                <div className="text-sm font-medium text-foreground group-hover:text-brand-600 transition-colors">{name}</div>
                                <div className="text-xs text-muted-foreground">{email}</div>
                              </div>
                            </Link>
                          </TableCell>
                          <TableCell>
                            {effective.role ? (
                              <Select defaultValue={effective.role}>
                                <SelectTrigger className="text-sm">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {config?.roles.map((r) => (
                                    <SelectItem key={r.name} value={r.name}>{r.name}</SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            ) : (
                              <span className="text-sm text-muted-foreground">No role</span>
                            )}
                          </TableCell>
                          <TableCell>
                            {effective.source === "direct" ? (
                              <Badge variant="secondary" className="text-[10px]">Direct</Badge>
                            ) : effective.source !== "none" ? (
                              <Badge className="text-[10px]" style={{
                                backgroundColor: allTeams.find((t) => t.name === effective.source)?.bg_color ?? "#F3F4F6",
                                color: allTeams.find((t) => t.name === effective.source)?.color ?? "#6B7280",
                              }}>
                                {effective.source}
                              </Badge>
                            ) : (
                              <span className="text-[10px] text-muted-foreground/50">&mdash;</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <span className="text-sm text-muted-foreground">
                              {assignment?.last_used ? formatRelativeTime(assignment.last_used) : "Never"}
                            </span>
                          </TableCell>
                          <TableCell className="text-right">
                            <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-red-500">Remove</Button>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              ) : (
                <div className="text-center py-6">
                  <p className="text-sm text-muted-foreground">No users assigned</p>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}

      {/* -- Tool-managed (OAuth) mode -- */}
      {authMode === "tool-managed" && config?.oauth && (
        <>
          {/* OAuth Configuration */}
          <Card>
            <CardContent>
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-sm font-heading font-semibold text-foreground">OAuth Configuration</h2>
                <Badge className={`text-[10px] ${
                  config.oauth.configured
                    ? "bg-emerald-50 text-emerald-600"
                    : "bg-amber-50 text-amber-600"
                } border-transparent`}>
                  {config.oauth.configured ? "Configured" : "Not configured"}
                </Badge>
              </div>

              <div className="space-y-4">
                <div>
                  <Label className="mb-1 text-xs text-muted-foreground">Client ID</Label>
                  <Input
                    type="text"
                    readOnly
                    value={config.oauth.client_id}
                    className="bg-muted font-mono"
                  />
                </div>
                <div>
                  <Label className="mb-1 text-xs text-muted-foreground">Redirect URI</Label>
                  <Input
                    type="text"
                    readOnly
                    value={config.oauth.redirect_uri}
                    className="bg-muted font-mono"
                  />
                </div>
                <div>
                  <Label className="mb-1 text-xs text-muted-foreground">Scopes</Label>
                  <div className="flex flex-wrap gap-1.5">
                    {config.oauth.scopes.map((scope) => (
                      <Badge key={scope} className="text-[11px] bg-blue-50 text-blue-600 font-mono border-transparent">
                        {scope}
                      </Badge>
                    ))}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Connected Users */}
          <Card>
            <CardContent>
              <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Connected Users</h2>

              {toolUsers.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow className="border-b border-border">
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">User</TableHead>
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Status</TableHead>
                      <TableHead className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Last Used</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {toolUsers.map(({ email, name, avatar, assignment }) => (
                      <TableRow key={email}>
                        <TableCell>
                          <div className="flex items-center gap-2.5">
                            <div className="w-7 h-7 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                              <span className="text-xs font-medium text-brand-700">{avatar}</span>
                            </div>
                            <div>
                              <div className="text-sm font-medium text-foreground">{name}</div>
                              <div className="text-xs text-muted-foreground">{email}</div>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={assignment?.oauth_status ?? null} />
                        </TableCell>
                        <TableCell>
                          <span className="text-sm text-muted-foreground">
                            {assignment?.last_used ? formatRelativeTime(assignment.last_used) : "Never"}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">
                          {assignment?.oauth_status === "connected" && (
                            <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-red-500">Revoke</Button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <div className="text-center py-6">
                  <p className="text-sm text-muted-foreground">No users have connected yet</p>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
