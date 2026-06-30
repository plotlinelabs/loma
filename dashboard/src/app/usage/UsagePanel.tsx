"use client";

import { useEffect, useState, useCallback } from "react";
import {
  fetchUsageStats,
  fetchAuthInfo,
  logout,
} from "../../lib/usage-api";
import type { UsageStats, UsageBucket, AuthInfo } from "../../lib/usage-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription, AlertAction } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { RiInformationLine, RiCloseLine } from "@remixicon/react";

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
    <Card>
      <CardContent>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-muted-foreground">{bucket.label}</h3>
          {isLimited && (
            <Badge variant="destructive">
              Rate Limited
            </Badge>
          )}
        </div>

        <div className="flex items-end gap-2 mb-2">
          <span className={`text-3xl font-bold ${utilizationColor(bucket.utilization)}`}>
            {remaining}%
          </span>
          <span className="text-sm text-muted-foreground mb-1">remaining</span>
        </div>

        <div className={`w-full h-2.5 rounded-full ${barBgColor(bucket.utilization)}`}>
          <div
            className={`h-full rounded-full transition-all duration-500 ${barColor(bucket.utilization)}`}
            style={{ width: `${pct}%` }}
          />
        </div>

        <div className="flex justify-between mt-2 text-xs text-muted-foreground">
          <span>{pct}% used</span>
          {showReset && bucket.reset > 0 && (
            <span title={formatResetDate(bucket.reset)}>
              Resets in {formatResetTime(bucket.reset)}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonCard() {
  return (
    <Card>
      <CardContent>
        <Skeleton className="h-3 w-32 mb-2" />
        <Skeleton className="h-8 w-20 mb-2" />
        <Skeleton className="h-2.5 w-full rounded-full mb-2" />
        <div className="flex justify-between">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-24" />
        </div>
      </CardContent>
    </Card>
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
      <Card>
        <CardContent>
          <Skeleton className="h-4 w-48 mb-2" />
          <Skeleton className="h-3 w-32" />
        </CardContent>
      </Card>
    );
  }

  if (!auth.loggedIn) {
    return (
      <Card className="ring-amber-300">
        <CardContent>
          <div className="flex items-center gap-2 mb-2">
            <div className="w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse" />
            <h3 className="text-sm font-medium text-amber-700">Not Logged In</h3>
          </div>
          <p className="text-sm text-muted-foreground">
            Run <code className="px-1.5 py-0.5 bg-muted rounded text-xs font-mono">claude auth login</code> in the terminal below to authenticate.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
            <div>
              <h3 className="text-sm font-medium text-foreground">
                {auth.email || "Authenticated"}
              </h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                {auth.authMethod === "claude.ai" ? "Claude MAX" : auth.authMethod}
                {auth.orgId && ` · Org ${auth.orgId.slice(0, 8)}...`}
              </p>
            </div>
          </div>
          <Button
            variant="ghost"
            size="xs"
            onClick={onLogout}
            className="text-muted-foreground hover:text-red-600"
          >
            Logout
          </Button>
        </div>
      </CardContent>
    </Card>
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
      <div className="flex items-center justify-end gap-2">
        {lastRefresh && (
          <span className="text-xs text-muted-foreground">
            Updated {lastRefresh.toLocaleTimeString()}
          </span>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={loadData}
          disabled={loading}
        >
          {loading ? "Refreshing..." : "Refresh"}
        </Button>
      </div>

      {/* Error banner */}
      {error && (
        <Alert variant="destructive">
          <RiInformationLine size={16} />
          <AlertDescription>{error}</AlertDescription>
          <AlertAction>
            <Button variant="ghost" size="icon-xs" onClick={() => setError(null)}>
              <RiCloseLine size={16} />
            </Button>
          </AlertAction>
        </Alert>
      )}

      {/* Auth Status */}
      <AuthStatusCard auth={auth} onLogout={handleLogout} />

      {/* Usage Cards */}
      {loading && !stats ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : stats ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <UsageBucketCard bucket={stats.session} />
          <UsageBucketCard bucket={stats.weekly} />
          <UsageBucketCard bucket={stats.weekly_sonnet} />
          <UsageBucketCard bucket={stats.overage} showReset={stats.overage.utilization > 0} />
        </div>
      ) : auth?.loggedIn === false ? (
        <div className="text-center py-6 text-muted-foreground">
          <p>Login to view usage statistics</p>
        </div>
      ) : null}

      {/* Detailed Info */}
      {stats && (
        <Card>
          <CardContent>
            <h3 className="text-sm font-medium text-muted-foreground mb-2">Details</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Session Reset</span>
                <span className="text-foreground">{formatResetDate(stats.session.reset)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Weekly Reset</span>
                <span className="text-foreground">{formatResetDate(stats.weekly.reset)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Overall Status</span>
                <span className={stats.overall_status === "allowed" ? "text-emerald-600" : "text-red-600"}>
                  {stats.overall_status === "allowed" ? "Active" : stats.overall_status}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Fallback %</span>
                <span className="text-foreground">{Math.round(stats.fallback_percentage * 100)}%</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
