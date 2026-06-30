"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  RiAddLine,
  RiLoader4Line,
} from "@remixicon/react";
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
  fetchTeam,
  fetchToolConfigs,
  type User,
  type Team,
  type ToolConfig,
} from "../../../../lib/governance-api";

/* ── Helpers ──────────────────────────────────────────────────────── */

const ALL_TOOLS = CATEGORIES.flatMap((c) => c.keys);

/* ── Page ─────────────────────────────────────────────────────────── */

export default function TeamDetailPage() {
  const params = useParams();
  const teamId = params.id as string;
  const [team, setTeam] = useState<Team | null>(null);
  const [members, setMembers] = useState<User[]>([]);
  const [toolConfigMap, setToolConfigMap] = useState<Record<string, ToolConfig>>({});
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    Promise.all([fetchTeam(teamId), fetchToolConfigs()])
      .then(([teamData, tc]) => {
        setTeam(teamData.team);
        setMembers(teamData.members);
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
                {Object.keys(team.tool_defaults).length} tool defaults
              </Badge>
            </div>
          </div>
        </div>
      </div>

      {/* Members */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-heading font-semibold text-foreground">Members</h2>
          <Button variant="ghost" size="xs" className="text-brand-600 hover:text-brand-700">
            <RiAddLine size={14} />
            Add Member
          </Button>
        </div>

        <div className="divide-y divide-border/50">
          {members.map((user) => (
            <div key={user.email} className="flex items-center gap-2 py-3 first:pt-0 last:pb-0">
              <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                <span className="text-sm font-medium text-brand-700">{user.avatar}</span>
              </div>
              <div className="flex-1 min-w-0">
                <Link
                  href={`/admin/${encodeURIComponent(user.email)}`}
                  className="text-sm font-medium text-foreground hover:text-brand-600 transition-colors"
                >
                  {user.name}
                </Link>
                <div className="text-xs text-muted-foreground">{user.email}</div>
              </div>
              <Button
                variant="ghost"
                size="xs"
                className="text-[10px] text-muted-foreground hover:text-red-500"
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      </div>

      {/* Tool Defaults — Loma-managed */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3">
        <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Tool Defaults — Loma-managed</h2>
        <p className="text-xs text-muted-foreground mb-2">
          Default roles assigned to all team members. Individual users can override these.
        </p>

        <Table>
          <TableHeader>
            <TableRow className="border-border">
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
                <TableRow key={toolKey} className="border-border/50">
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
                      <span className="text-sm font-medium text-foreground group-hover:text-brand-600 transition-colors">
                        {meta.displayName}
                      </span>
                    </Link>
                  </TableCell>
                  <TableCell className="pr-4">
                    {defaultRole ? (
                      <Select defaultValue={defaultRole}>
                        <SelectTrigger className="text-sm h-7 w-auto">
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
                    <span className="text-sm text-muted-foreground">{team.members.length}</span>
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
        <h2 className="text-sm font-heading font-semibold text-foreground mb-2">Tool Defaults — OAuth</h2>
        <p className="text-xs text-muted-foreground mb-2">
          Whether team members are required to connect their accounts via OAuth.
        </p>

        <Table>
          <TableHeader>
            <TableRow className="border-border">
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
                <TableRow key={toolKey} className="border-border/50">
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
                      <span className="text-sm font-medium text-foreground group-hover:text-brand-600 transition-colors">
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
                    <span className={`text-sm font-medium ${
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
