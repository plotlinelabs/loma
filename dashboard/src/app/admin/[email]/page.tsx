"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { RiLoader4Line } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { getToolMeta, CATEGORIES } from "../../mcp/tool-meta";
import { TOOL_LOGOS } from "../../mcp/tool-logos";
import {
  fetchUser,
  fetchToolConfigs,
  getEffectiveRole,
  formatRelativeTime,
  type User,
  type Team,
  type ToolConfig,
} from "../../../lib/governance-api";

/* ── Helpers ──────────────────────────────────────────────────────── */

const ALL_TOOLS = CATEGORIES.flatMap((c) => c.keys);

/* ── Page ─────────────────────────────────────────────────────────── */

export default function UserDetailPage() {
  const params = useParams();
  const email = decodeURIComponent(params.email as string);
  const [user, setUser] = useState<User | null>(null);
  const [userTeams, setUserTeams] = useState<Team[]>([]);
  const [toolConfigMap, setToolConfigMap] = useState<Record<string, ToolConfig>>({});
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    Promise.all([fetchUser(email), fetchToolConfigs()])
      .then(([userData, tc]) => {
        setUser(userData.user);
        setUserTeams(userData.teams);
        const map: Record<string, ToolConfig> = {};
        for (const c of tc) map[c.tool_key] = c;
        setToolConfigMap(map);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [email]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RiLoader4Line size={32} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (notFound || !user) {
    return (
      <div className="space-y-4 animate-fade-in-up">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink href="/admin">Admin</BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>User not found</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        <div className="bg-card rounded-xl border border-border p-6 text-center">
          <p className="text-muted-foreground">User not found</p>
        </div>
      </div>
    );
  }

  const userTools = user.tool_assignments ?? {};

  // Count tools with access
  const toolsWithAccess = ALL_TOOLS.filter((t) => {
    const a = userTools[t];
    if (!a) {
      // Check team defaults for Loma-managed tools
      const eff = getEffectiveRole(user, userTeams, t);
      return eff.role !== null;
    }
    return a.role || a.oauth_status === "connected";
  }).length;

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
            <BreadcrumbPage>{user.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* User Header */}
      <div className="bg-card rounded-xl border border-border p-2 md:p-3">
        <div className="flex items-center gap-3">
          <div className="w-14 h-14 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
            <span className="text-xl font-semibold text-brand-700">{user.avatar}</span>
          </div>
          <div className="flex-1">
            <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">{user.name}</h1>
            <p className="text-[13px] text-muted-foreground">{user.email}</p>

            {/* Team badges */}
            {userTeams.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {userTeams.map((team) => (
                  <Link
                    key={team.team_id}
                    href={`/admin/teams/${team.team_id}`}
                    className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full font-medium transition-opacity hover:opacity-80"
                    style={{ backgroundColor: team.bg_color, color: team.color }}
                  >
                    {team.name}
                  </Link>
                ))}
              </div>
            )}
          </div>
          <div className="ml-auto flex gap-2">
            <div className="text-center">
              <div className="text-xl font-semibold text-foreground">{toolsWithAccess}</div>
              <div className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">Tools</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-semibold text-foreground">{userTeams.length}</div>
              <div className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">Teams</div>
            </div>
          </div>
        </div>
      </div>

      {/* Tool Cards Grid */}
      {CATEGORIES.map((cat) => (
        <div key={cat.name}>
          <h2 className="text-[11px] font-heading font-bold uppercase tracking-wider text-muted-foreground mb-2 ml-1">{cat.name}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 stagger-children">
            {cat.keys.map((toolKey) => {
              const meta = getToolMeta(toolKey);
              const Logo = TOOL_LOGOS[toolKey];
              const toolConfig = toolConfigMap[toolKey];
              const assignment = userTools[toolKey];
              const isManaged = toolConfig?.auth_mode === "loma-managed";

              // Resolve effective permission with provenance
              const effective = isManaged ? getEffectiveRole(user, userTeams, toolKey) : null;
              const hasAccess = isManaged
                ? effective?.role !== null
                : assignment?.oauth_status === "connected";

              return (
                <Link
                  key={toolKey}
                  href={`/mcp/${toolKey}`}
                  className={`bg-card rounded-xl border p-3 flex items-start gap-2 transition-all duration-200 hover-lift group ${
                    hasAccess ? "border-border" : "border-border/50 opacity-60"
                  }`}
                >
                  {/* Tool icon */}
                  <div
                    className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
                    style={{ backgroundColor: meta.bgColor }}
                  >
                    {Logo ? (
                      <Logo className="w-5 h-5" />
                    ) : (
                      <span className="text-[13px] font-bold" style={{ color: meta.color }}>
                        {meta.displayName.charAt(0)}
                      </span>
                    )}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium text-foreground group-hover:text-brand-600 transition-colors">
                        {meta.displayName}
                      </span>
                    </div>

                    {/* Auth mode badge */}
                    <div className="flex items-center gap-1.5 mt-1">
                      <Badge variant="secondary" className={`text-[9px] px-1.5 py-px ${
                        isManaged
                          ? "bg-violet-50 text-violet-500"
                          : "bg-blue-50 text-blue-500"
                      }`}>
                        {isManaged ? "Loma-managed" : "OAuth"}
                      </Badge>
                    </div>

                    {/* Permission level with provenance */}
                    <div className="mt-2">
                      {isManaged ? (
                        effective?.role ? (
                          <div className="flex flex-col gap-0.5">
                            <Select
                              defaultValue={effective.role}
                              onValueChange={() => {}}
                            >
                              <SelectTrigger
                                className="text-xs h-7 w-auto"
                                onClick={(e) => e.preventDefault()}
                              >
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {toolConfig?.roles.map((r) => (
                                  <SelectItem key={r.name} value={r.name}>{r.name}</SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            {effective.source !== "direct" && effective.source !== "none" && (
                              <span className="text-[9px] text-muted-foreground">
                                via {effective.source}
                              </span>
                            )}
                            {effective.source === "direct" && (
                              <span className="text-[9px] text-muted-foreground">
                                direct assignment
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">No role assigned</span>
                        )
                      ) : (
                        <span className={`text-xs font-medium ${
                          assignment?.oauth_status === "connected"
                            ? "text-emerald-600"
                            : assignment?.oauth_status === "expired"
                            ? "text-amber-600"
                            : "text-muted-foreground"
                        }`}>
                          {assignment?.oauth_status === "connected"
                            ? "Connected"
                            : assignment?.oauth_status === "expired"
                            ? "Token expired"
                            : "Not connected"}
                        </span>
                      )}
                    </div>

                    {/* Last activity */}
                    {assignment?.last_used && (
                      <div className="text-[10px] text-muted-foreground mt-1">
                        Last used {formatRelativeTime(assignment.last_used)}
                      </div>
                    )}
                  </div>

                  {/* Action indicator */}
                  <div className="flex-shrink-0 mt-1">
                    {isManaged && effective?.role && (
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={(e) => { e.preventDefault(); }}
                        className="text-[10px] text-muted-foreground hover:text-red-500 relative z-10"
                      >
                        Revoke
                      </Button>
                    )}
                    {!isManaged && assignment?.oauth_status === "connected" && (
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={(e) => { e.preventDefault(); }}
                        className="text-[10px] text-muted-foreground hover:text-red-500 relative z-10"
                      >
                        Revoke
                      </Button>
                    )}
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
