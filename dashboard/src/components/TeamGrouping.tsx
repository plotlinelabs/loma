"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";
import { fetchTeamDirectory, type TeamDirectoryEntry } from "../lib/governance-api";

export type GroupBy = "scope" | "team";

export const NO_TEAM = "__none__";

/** Team directory for grouping; failures fall back to an empty list. */
export function useTeamDirectory(): { teams: TeamDirectoryEntry[]; reload: () => void } {
  const [teams, setTeams] = useState<TeamDirectoryEntry[]>([]);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    fetchTeamDirectory().then(setTeams).catch(() => setTeams([]));
  }, [version]);
  return { teams, reload: () => setVersion((v) => v + 1) };
}

const groupByListeners = new Set<() => void>();
const subscribeGroupBy = (notify: () => void) => {
  groupByListeners.add(notify);
  return () => { groupByListeners.delete(notify); };
};

/** Persisted "group by" preference per page; falls back until the user picks one. */
export function useGroupBy(storageKey: string, fallback: GroupBy): [GroupBy, (g: GroupBy) => void] {
  const saved = useSyncExternalStore(
    subscribeGroupBy,
    () => window.localStorage.getItem(storageKey),
    () => null,
  );
  const value: GroupBy = saved === "scope" || saved === "team" ? saved : fallback;
  return [value, (g: GroupBy) => {
    window.localStorage.setItem(storageKey, g);
    groupByListeners.forEach((notify) => notify());
  }];
}

/** Teams the user belongs to first, then the rest, each alphabetical. */
export function orderTeams(teams: TeamDirectoryEntry[], userEmail: string): TeamDirectoryEntry[] {
  const email = userEmail.toLowerCase();
  const mine = (t: TeamDirectoryEntry) => t.members.some((m) => m.toLowerCase() === email);
  return [...teams].sort((a, b) => Number(mine(b)) - Number(mine(a)) || a.name.localeCompare(b.name));
}

export function TeamSwatch({ team, size = 14 }: { team?: Pick<TeamDirectoryEntry, "name" | "color" | "bg_color"> | null; size?: number }) {
  return (
    <span
      className="inline-flex items-center justify-center rounded flex-shrink-0 font-bold"
      style={{
        width: size,
        height: size,
        fontSize: Math.max(8, size * 0.6),
        backgroundColor: team?.bg_color || "var(--muted)",
        color: team?.color || "var(--muted-foreground)",
      }}
    >
      {team ? team.name.charAt(0).toUpperCase() : "–"}
    </span>
  );
}

export function GroupByToggle({ value, onChange, className }: { value: GroupBy; onChange: (g: GroupBy) => void; className?: string }) {
  return (
    <div className={cn("flex items-center bg-card border border-border rounded-md p-0.5 text-[11px]", className)} data-testid="group-by-toggle">
      {(["scope", "team"] as const).map((g) => (
        <button
          key={g}
          type="button"
          onClick={() => onChange(g)}
          data-testid={`group-by-${g}`}
          className={cn(
            "px-2 py-0.5 rounded capitalize transition-colors",
            value === g ? "bg-accent text-accent-foreground font-medium" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {g === "scope" ? "Scope" : "Team"}
        </button>
      ))}
    </div>
  );
}
