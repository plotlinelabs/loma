"use client";

import { useEffect, useState, useCallback } from "react";
import {
  fetchUsageStats,
  fetchAuthInfo,
  logout,
} from "../../lib/usage-api";
import type { UsageStats, UsageBucket, AuthInfo } from "../../lib/usage-api";

function formatResetTime(unixTimestamp: number): string {
  if (!unixTimestamp) return "Unknown";
  const now = Date.now() / 1000;
  const diff = unixTimestamp - now;
  if (diff <= 0) return "Resetting...";
  const hours = Math.floor(diff / 3600);
  const minutes = Math.floor((diff % 3600) / 60);
  if (hours > 24) {
    const days = Math.floor(hours / 24);
    const remainingHours = hours % 24;
    return `${days}d ${remainingHours}h`;
  }
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatResetDate(unixTimestamp: number): string {
  if (!unixTimestamp) return "";
  return new Date(unixTimestamp * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

function utilizationColor(util: number): string {
  if (util >= 0.75) return "text-red-600";
  if (util >= 0.5) return "text-amber-600";
  return "text-emerald-600";
}

function barColor(util: number): string {
  if (util >= 0.75) return "bg-red-500";
  if (util >= 0.5) return "bg-amber-500";
  return "bg-emerald-500";
}

function barBgColor(util: number): string {
  if (util >= 0.75) return "bg-red-100";
  if (util >= 0.5) return "bg-amber-100";
  return "bg-emerald-100";
}

function UsageBucketCard({ bucket, showReset = true }: { bucket: UsageBucket; showReset?: boolean }) {
  const pct = Math.round(bucket.utilization * 100);
  const remaining = 100 - pct;
  const isLimited = bucket.status !== "allowed";

  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-gray-600">{bucket.label}</h3>
        {isLimited && (
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-red-100 text-red-700">
            Rate Limited
          </span>
        )}
      </div>

      <div className="flex items-end gap-2 mb-3">
        <span className={`text-3xl font-bold ${utilizationColor(bucket.utilization)}`}>
          {remaining}%
        </span>
        <span className="text-sm text-gray-500 mb-1">remaining</span>
      </div>

      <div className={`w-full h-2.5 rounded-full ${barBgColor(bucket.utilization)}`}>
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor(bucket.utilization)}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="flex justify-between mt-2 text-xs text-gray-500">
        <span>{pct}% used</span>
        {showReset && bucket.reset > 0 && (
          <span title={formatResetDate(bucket.reset)}>
            Resets in {formatResetTime(bucket.reset)}
          </span>
        )}
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-5">
      <div className="skeleton h-3 w-32 mb-4" />
      <div className="skeleton h-8 w-20 mb-3" />
      <div className="skeleton h-2.5 w-full rounded-full mb-2" />
      <div className="flex justify-between">
        <div className="skeleton h-3 w-16" />
        <div className="skeleton h-3 w-24" />
      </div>
    </div>
  );
}

function AuthStatusCard({
  auth,
  onLogout,
}: {
  auth: AuthInfo | null;
  onLogout: () => void;
}) {
  if (auth === null) {
    return (
      <div className="bg-surface rounded-xl border border-gray-200 p-5">
        <div className="skeleton h-4 w-48 mb-2" />
        <div className="skeleton h-3 w-32" />
      </div>
    );
  }

  if (!auth.loggedIn) {
    return (
      <div className="bg-surface rounded-xl border border-amber-300 p-5">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse" />
          <h3 className="text-sm font-medium text-amber-700">Not Logged In</h3>
        </div>
        <p className="text-sm text-gray-600">
          Run <code className="px-1.5 py-0.5 bg-gray-100 rounded text-xs font-mono">claude auth login</code> in the terminal below to authenticate.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-5">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
          <div>
            <h3 className="text-sm font-medium text-gray-900">
              {auth.email || "Authenticated"}
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {auth.authMethod === "claude.ai" ? "Claude MAX" : auth.authMethod}
              {auth.orgId && ` \u00b7 Org ${auth.orgId.slice(0, 8)}...`}
            </p>
          </div>
        </div>
        <button
          onClick={onLogout}
          className="text-xs text-gray-500 hover:text-red-600 transition-colors"
        >
          Logout
        </button>
      </div>
    </div>
  );
}

export default function UsagePanel() {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [auth, setAuth] = useState<AuthInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const loadData = useCallback(async () => {
    try {
      setError(null);
      const [usageStats, authInfo] = await Promise.all([
        fetchUsageStats().catch(() => null),
        fetchAuthInfo().catch(() => ({ loggedIn: false }) as AuthInfo),
      ]);
      if (usageStats) setStats(usageStats);
      setAuth(authInfo);
      setLastRefresh(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load usage data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [loadData]);

  const handleLogout = async () => {
    if (!confirm("Are you sure you want to logout? Loma will stop working until you login again.")) return;
    try {
      await logout();
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Logout failed");
    }
  };

  return (
    <div className="space-y-4">
      {/* Refresh row */}
      <div className="flex items-center justify-end gap-3">
        {lastRefresh && (
          <span className="text-xs text-gray-400">
            Updated {lastRefresh.toLocaleTimeString()}
          </span>
        )}
        <button
          onClick={loadData}
          disabled={loading}
          className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
          <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
          </svg>
          {error}
          <button onClick={() => setError(null)} className="ml-auto text-red-400 hover:text-red-600">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>
      )}

      {/* Auth Status */}
      <AuthStatusCard auth={auth} onLogout={handleLogout} />

      {/* Usage Cards */}
      {loading && !stats ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : stats ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <UsageBucketCard bucket={stats.session} />
          <UsageBucketCard bucket={stats.weekly} />
          <UsageBucketCard bucket={stats.weekly_sonnet} />
          <UsageBucketCard bucket={stats.overage} showReset={stats.overage.utilization > 0} />
        </div>
      ) : auth?.loggedIn === false ? (
        <div className="text-center py-12 text-gray-500">
          <p>Login to view usage statistics</p>
        </div>
      ) : null}

      {/* Detailed Info */}
      {stats && (
        <div className="bg-surface rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-600 mb-3">Details</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">Session Reset</span>
              <span className="text-gray-900">{formatResetDate(stats.session.reset)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Weekly Reset</span>
              <span className="text-gray-900">{formatResetDate(stats.weekly.reset)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Overall Status</span>
              <span className={stats.overall_status === "allowed" ? "text-emerald-600" : "text-red-600"}>
                {stats.overall_status === "allowed" ? "Active" : stats.overall_status}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Fallback %</span>
              <span className="text-gray-900">{Math.round(stats.fallback_percentage * 100)}%</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
