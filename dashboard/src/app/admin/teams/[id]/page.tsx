"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
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
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900" />
      </div>
    );
  }

  if (notFound || !team) {
    return (
      <div className="space-y-4 animate-fade-in-up">
        <Link href="/admin" className="text-sm text-gray-500 hover:text-gray-700 transition-colors flex items-center gap-1">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
          Back to Admin
        </Link>
        <div className="bg-surface rounded-xl border border-gray-200 p-12 text-center">
          <p className="text-gray-500">Team not found</p>
        </div>
      </div>
    );
  }

  const lomaTools = ALL_TOOLS.filter((t) => toolConfigMap[t]?.auth_mode === "loma-managed");
  const oauthTools = ALL_TOOLS.filter((t) => toolConfigMap[t]?.auth_mode === "tool-managed");

  return (
    <div className="space-y-5 animate-fade-in-up">
      {/* Back link */}
      <Link href="/admin" className="text-sm text-gray-500 hover:text-gray-700 transition-colors flex items-center gap-1">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
        </svg>
        Back to Admin
      </Link>

      {/* Team Header */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
        <div className="flex items-center gap-4">
          <div
            className="w-14 h-14 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ backgroundColor: team.bg_color }}
          >
            <span className="text-2xl font-bold" style={{ color: team.color }}>
              {team.name.charAt(0)}
            </span>
          </div>
          <div className="flex-1">
            <h1 className="text-lg md:text-xl font-semibold text-gray-900">{team.name}</h1>
            <div className="flex items-center gap-2 mt-1">
              <span
                className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                style={{ backgroundColor: team.bg_color, color: team.color }}
              >
                {team.members.length} members
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">
                {Object.keys(team.tool_defaults).length} tool defaults
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Members */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-900">Members</h2>
          <button className="text-xs font-medium text-brand-600 hover:text-brand-700 transition-colors flex items-center gap-1">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
            Add Member
          </button>
        </div>

        <div className="divide-y divide-gray-100">
          {members.map((user) => (
            <div key={user.email} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
              <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center flex-shrink-0">
                <span className="text-sm font-medium text-brand-700">{user.avatar}</span>
              </div>
              <div className="flex-1 min-w-0">
                <Link
                  href={`/admin/${encodeURIComponent(user.email)}`}
                  className="text-sm font-medium text-gray-900 hover:text-brand-600 transition-colors"
                >
                  {user.name}
                </Link>
                <div className="text-xs text-gray-400">{user.email}</div>
              </div>
              <button className="text-[10px] text-gray-400 hover:text-red-500 transition-colors">
                Remove
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Tool Defaults — Loma-managed */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-4">Tool Defaults — Loma-managed</h2>
        <p className="text-xs text-gray-400 mb-4">
          Default roles assigned to all team members. Individual users can override these.
        </p>

        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Tool</th>
                <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Default Role</th>
                <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Members Affected</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {lomaTools.map((toolKey) => {
                const meta = getToolMeta(toolKey);
                const Logo = TOOL_LOGOS[toolKey];
                const toolConfig = toolConfigMap[toolKey];
                const td = team.tool_defaults[toolKey];
                const defaultRole = td?.role ?? null;

                return (
                  <tr key={toolKey} className="border-b border-gray-100 last:border-0">
                    <td className="py-3 pr-4">
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
                        <span className="text-sm font-medium text-gray-900 group-hover:text-brand-600 transition-colors">
                          {meta.displayName}
                        </span>
                      </Link>
                    </td>
                    <td className="py-3 pr-4">
                      {defaultRole ? (
                        <select
                          defaultValue={defaultRole}
                          className="text-sm border border-gray-200 rounded-lg px-2 py-1 bg-surface text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent-200"
                        >
                          {toolConfig?.roles.map((r) => (
                            <option key={r.name} value={r.name}>{r.name}</option>
                          ))}
                          <option value="">No access</option>
                        </select>
                      ) : (
                        <span className="text-xs text-gray-400">No default</span>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <span className="text-sm text-gray-500">{team.members.length}</span>
                    </td>
                    <td className="py-3 text-right">
                      {defaultRole && (
                        <button className="text-xs text-gray-400 hover:text-red-500 transition-colors">
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Tool Defaults — OAuth */}
      <div className="bg-surface rounded-xl border border-gray-200 p-5 md:p-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-4">Tool Defaults — OAuth</h2>
        <p className="text-xs text-gray-400 mb-4">
          Whether team members are required to connect their accounts via OAuth.
        </p>

        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Tool</th>
                <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">OAuth Required</th>
                <th className="pb-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Connected</th>
              </tr>
            </thead>
            <tbody>
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
                  <tr key={toolKey} className="border-b border-gray-100 last:border-0">
                    <td className="py-3 pr-4">
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
                        <span className="text-sm font-medium text-gray-900 group-hover:text-brand-600 transition-colors">
                          {meta.displayName}
                        </span>
                      </Link>
                    </td>
                    <td className="py-3 pr-4">
                      {oauthRequired ? (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">
                          Required
                        </span>
                      ) : (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-400 font-medium">
                          Optional
                        </span>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <span className={`text-sm font-medium ${
                        connectedCount === team.members.length ? "text-emerald-600" : "text-gray-500"
                      }`}>
                        {connectedCount}/{team.members.length}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
