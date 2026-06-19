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

/* ── Helpers ──────────────────────────────────────────────────────── */

function getAllToolKeys(): string[] {
  return CATEGORIES.flatMap((c) => c.keys);
}

/* ── Components ───────────────────────────────────────────────────── */

function StatusBadge({ status }: { status: "connected" | "expired" | "not_connected" | null }) {
  if (status === "connected")
    return <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">Connected</span>;
  if (status === "expired")
    return <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 font-medium">Expired</span>;
  return <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">Not connected</span>;
}

function RoleRow({ role }: { role: { name: string; description: string } }) {
  return (
    <tr className="border-b border-gray-100 last:border-0">
      <td className="py-3 pr-4">
        <span className="text-sm font-medium text-gray-900">{role.name}</span>
      </td>
      <td className="py-3 pr-4">
        <span className="text-sm text-gray-500">{role.description}</span>
      </td>
      <td className="py-3 text-right">
        <button className="text-xs text-gray-400 hover:text-red-500 transition-colors">Remove</button>
      </td>
    </tr>
  );
}

/* ── Main Page ────────────────────────────────────────────────────── */

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
        <Link href="/mcp" className="text-sm text-gray-500 hover:text-gray-700 transition-colors flex items-center gap-1">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
          Back to Graph
        </Link>
        <div className="bg-surface rounded-xl border border-gray-200 p-12 text-center">
          <p className="text-gray-500">Tool not found</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900" />
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
    <div className="space-y-5 animate-fade-in-up">
      {/* Back link */}
      <Link href="/mcp" className="text-sm text-gray-500 hover:text-gray-700 transition-colors flex items-center gap-1">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
        </svg>
        Back to Graph
      </Link>

      {/* Tool Header */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
        <div className="flex items-center gap-4">
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
            <h1 className="text-lg md:text-xl font-semibold text-gray-900">{meta.displayName}</h1>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] px-2 py-0.5 rounded-full font-medium" style={{ backgroundColor: meta.bgColor, color: meta.color }}>
                {meta.authMethod}
              </span>
              {meta.supportsOAuth && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">
                  OAuth supported
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Auth Mode Toggle */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-3">Authentication Mode</h2>
        <div className="inline-flex rounded-lg border border-gray-200 p-0.5 bg-gray-50">
          <button
            onClick={() => setAuthMode("loma-managed")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
              authMode === "loma-managed"
                ? "bg-surface text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            Loma-managed
          </button>
          <button
            onClick={() => setAuthMode("tool-managed")}
            disabled={!meta.supportsOAuth}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
              authMode === "tool-managed"
                ? "bg-surface text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            } ${!meta.supportsOAuth ? "opacity-40 cursor-not-allowed" : ""}`}
          >
            Tool-managed (OAuth)
          </button>
        </div>
        {!meta.supportsOAuth && (
          <p className="text-xs text-gray-400 mt-2">
            This tool does not support OAuth. Only Loma-managed authentication is available.
          </p>
        )}
      </div>

      {/* ── Loma-managed mode ── */}
      {authMode === "loma-managed" && (
        <>
          {/* Roles */}
          <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-gray-900">Roles</h2>
              <button className="text-xs font-medium text-brand-600 hover:text-brand-700 transition-colors flex items-center gap-1">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                </svg>
                Add Role
              </button>
            </div>

            {config?.roles && config.roles.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Role</th>
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Description</th>
                      <th className="pb-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {config.roles.map((role) => (
                      <RoleRow key={role.name} role={role} />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-8">
                <p className="text-sm text-gray-400">No roles defined yet</p>
                <p className="text-xs text-gray-300 mt-1">Add a role to start managing access</p>
              </div>
            )}
          </div>

          {/* Team Assignments */}
          {(() => {
            const teamsWithDefaults = allTeams.filter((t) => t.tool_defaults[toolKey]?.role);
            return teamsWithDefaults.length > 0 ? (
              <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
                <h2 className="text-sm font-semibold text-gray-900 mb-4">Team Assignments</h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-gray-200">
                        <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Team</th>
                        <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Default Role</th>
                        <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Members</th>
                        <th className="pb-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {teamsWithDefaults.map((team) => (
                        <tr key={team.team_id} className="border-b border-gray-100 last:border-0">
                          <td className="py-3 pr-4">
                            <Link href={`/admin/teams/${team.team_id}`} className="flex items-center gap-2.5 group">
                              <div
                                className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
                                style={{ backgroundColor: team.bg_color }}
                              >
                                <span className="text-xs font-bold" style={{ color: team.color }}>
                                  {team.name.charAt(0)}
                                </span>
                              </div>
                              <span className="text-sm font-medium text-gray-900 group-hover:text-brand-600 transition-colors">
                                {team.name}
                              </span>
                            </Link>
                          </td>
                          <td className="py-3 pr-4">
                            <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">
                              {team.tool_defaults[toolKey]?.role}
                            </span>
                          </td>
                          <td className="py-3 pr-4">
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
                                <span className="text-[10px] text-gray-400 ml-0.5">+{team.members.length - 4}</span>
                              )}
                            </div>
                          </td>
                          <td className="py-3 text-right">
                            <button className="text-xs text-gray-400 hover:text-red-500 transition-colors">Remove</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null;
          })()}

          {/* User Assignments */}
          <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">User Assignments</h2>

            {toolUsers.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">User</th>
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Effective Role</th>
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Source</th>
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Last Used</th>
                      <th className="pb-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {toolUsers.map(({ email, name, avatar, assignment }) => {
                      const user = users.find((u) => u.email === email)!;
                      const effective = getEffectiveRole(user, allTeams, toolKey);
                      return (
                        <tr key={email} className="border-b border-gray-100 last:border-0">
                          <td className="py-3 pr-4">
                            <Link href={`/admin/${encodeURIComponent(email)}`} className="flex items-center gap-2.5 group">
                              <div className="w-7 h-7 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                                <span className="text-xs font-medium text-brand-700">{avatar}</span>
                              </div>
                              <div>
                                <div className="text-sm font-medium text-gray-900 group-hover:text-brand-600 transition-colors">{name}</div>
                                <div className="text-xs text-gray-400">{email}</div>
                              </div>
                            </Link>
                          </td>
                          <td className="py-3 pr-4">
                            {effective.role ? (
                              <select
                                defaultValue={effective.role}
                                className="text-sm border border-gray-200 rounded-lg px-2 py-1 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                              >
                                {config?.roles.map((r) => (
                                  <option key={r.name} value={r.name}>{r.name}</option>
                                ))}
                              </select>
                            ) : (
                              <span className="text-sm text-gray-400">No role</span>
                            )}
                          </td>
                          <td className="py-3 pr-4">
                            {effective.source === "direct" ? (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">Direct</span>
                            ) : effective.source !== "none" ? (
                              <span className="text-[10px] px-2 py-0.5 rounded-full font-medium" style={{
                                backgroundColor: allTeams.find((t) => t.name === effective.source)?.bg_color ?? "#F3F4F6",
                                color: allTeams.find((t) => t.name === effective.source)?.color ?? "#6B7280",
                              }}>
                                {effective.source}
                              </span>
                            ) : (
                              <span className="text-[10px] text-gray-300">&mdash;</span>
                            )}
                          </td>
                          <td className="py-3 pr-4">
                            <span className="text-sm text-gray-500">
                              {assignment?.last_used ? formatRelativeTime(assignment.last_used) : "Never"}
                            </span>
                          </td>
                          <td className="py-3 text-right">
                            <button className="text-xs text-gray-400 hover:text-red-500 transition-colors">Remove</button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-8">
                <p className="text-sm text-gray-400">No users assigned</p>
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Tool-managed (OAuth) mode ── */}
      {authMode === "tool-managed" && config?.oauth && (
        <>
          {/* OAuth Configuration */}
          <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-gray-900">OAuth Configuration</h2>
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                config.oauth.configured
                  ? "bg-emerald-50 text-emerald-600"
                  : "bg-amber-50 text-amber-600"
              }`}>
                {config.oauth.configured ? "Configured" : "Not configured"}
              </span>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Client ID</label>
                <input
                  type="text"
                  readOnly
                  value={config.oauth.client_id}
                  className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 text-gray-700 font-mono"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Redirect URI</label>
                <input
                  type="text"
                  readOnly
                  value={config.oauth.redirect_uri}
                  className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 text-gray-700 font-mono"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">Scopes</label>
                <div className="flex flex-wrap gap-1.5">
                  {config.oauth.scopes.map((scope) => (
                    <span key={scope} className="text-[11px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 font-mono font-medium">
                      {scope}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Connected Users */}
          <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">Connected Users</h2>

            {toolUsers.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">User</th>
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Status</th>
                      <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Last Used</th>
                      <th className="pb-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {toolUsers.map(({ email, name, avatar, assignment }) => (
                      <tr key={email} className="border-b border-gray-100 last:border-0">
                        <td className="py-3 pr-4">
                          <div className="flex items-center gap-2.5">
                            <div className="w-7 h-7 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                              <span className="text-xs font-medium text-brand-700">{avatar}</span>
                            </div>
                            <div>
                              <div className="text-sm font-medium text-gray-900">{name}</div>
                              <div className="text-xs text-gray-400">{email}</div>
                            </div>
                          </div>
                        </td>
                        <td className="py-3 pr-4">
                          <StatusBadge status={assignment?.oauth_status ?? null} />
                        </td>
                        <td className="py-3 pr-4">
                          <span className="text-sm text-gray-500">
                            {assignment?.last_used ? formatRelativeTime(assignment.last_used) : "Never"}
                          </span>
                        </td>
                        <td className="py-3 text-right">
                          {assignment?.oauth_status === "connected" && (
                            <button className="text-xs text-gray-400 hover:text-red-500 transition-colors">Revoke</button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-8">
                <p className="text-sm text-gray-400">No users have connected yet</p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
