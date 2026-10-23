"use client";

import { useState } from "react";
import Link from "next/link";
import type { Flow } from "../../../lib/api";
import type { TeamDirectoryEntry } from "../../../lib/governance-api";
import { GroupByToggle, NO_TEAM, TeamSwatch, type GroupBy } from "../../../components/TeamGrouping";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import { RiArrowRightSLine, RiFolderLine, RiFolderOpenLine, RiStackLine } from "@remixicon/react";
import FlowOrganizeMenu from "./FlowOrganizeMenu";

export interface FlowSelection {
  section: string | null;
  folder: string | null;
}

/** Tree section a flow belongs to for the current grouping (`team:<id>` or `scope:<shared|private>`). */
export function flowSectionKey(flow: Flow, groupBy: GroupBy, teamIds: Set<string>): string {
  if (groupBy === "team") return `team:${flow.team_id && teamIds.has(flow.team_id) ? flow.team_id : NO_TEAM}`;
  return `scope:${flow.visibility === "private" ? "private" : "shared"}`;
}

const STATUS_DOT: Record<string, string> = {
  active: "bg-emerald-500",
  paused: "bg-amber-400",
  completed: "bg-muted-foreground/40",
};

export default function FlowTreeSidebar({
  flows,
  teams,
  groupBy,
  onGroupByChange,
  selection,
  onSelect,
  canOrganize,
  folders,
  onFlowChanged,
}: {
  flows: Flow[];
  teams: TeamDirectoryEntry[];
  groupBy: GroupBy;
  onGroupByChange: (g: GroupBy) => void;
  selection: FlowSelection;
  onSelect: (selection: FlowSelection) => void;
  canOrganize: (flow: Flow) => boolean;
  folders: string[];
  onFlowChanged: (flow: Flow) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());
  const teamIds = new Set(teams.map((t) => t.team_id));
  const teamById = new Map(teams.map((t) => [t.team_id, t]));

  const sections: { key: string; label: string; team?: TeamDirectoryEntry | null }[] =
    groupBy === "team"
      ? [...teams.map((t) => ({ key: `team:${t.team_id}`, label: t.name, team: t })), { key: `team:${NO_TEAM}`, label: "No team", team: null }]
      : [{ key: "scope:shared", label: "Shared" }, { key: "scope:private", label: "Private" }];

  const bySection = new Map<string, { ungrouped: Flow[]; folders: Map<string, Flow[]> }>();
  for (const s of sections) bySection.set(s.key, { ungrouped: [], folders: new Map() });
  for (const flow of flows) {
    const group = bySection.get(flowSectionKey(flow, groupBy, teamIds));
    if (!group) continue;
    if (flow.folder) {
      if (!group.folders.has(flow.folder)) group.folders.set(flow.folder, []);
      group.folders.get(flow.folder)!.push(flow);
    } else {
      group.ungrouped.push(flow);
    }
  }

  function toggle(set: Set<string>, key: string, update: (s: Set<string>) => void) {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    update(next);
  }

  function renderFlowRow(flow: Flow) {
    const team = groupBy === "scope" && flow.team_id ? teamById.get(flow.team_id) : undefined;
    return (
      <div
        key={flow.flow_id}
        className="group flex items-center gap-1.5 px-3 py-1.5 rounded-md mx-1 text-foreground/80 hover:bg-muted transition-colors"
      >
        <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0 ml-1.5", STATUS_DOT[flow.status] || STATUS_DOT.completed)} />
        <Link href={`/flows/${flow.flow_id}`} className="flex-1 text-left text-[13px] truncate" title={flow.name}>
          {flow.name}
        </Link>
        {team && <span title={team.name} className="flex-shrink-0"><TeamSwatch team={team} /></span>}
        {canOrganize(flow) && (
          <FlowOrganizeMenu
            flow={flow}
            teams={teams}
            folders={folders}
            onChanged={onFlowChanged}
            triggerClassName="p-0.5 rounded flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
          />
        )}
      </div>
    );
  }

  return (
    <div className="hidden md:flex w-[280px] flex-shrink-0 bg-muted/30 border-r border-border flex-col h-full overflow-hidden" data-testid="flow-tree">
      <div className="px-4 pt-3 pb-2">
        <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Flows</h1>
      </div>
      <div className="flex items-center gap-2 px-4 pb-2 text-[11px] text-muted-foreground">
        <span>Group by</span>
        <GroupByToggle value={groupBy} onChange={onGroupByChange} />
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="py-2">
          <button
            onClick={() => onSelect({ section: null, folder: null })}
            className={cn(
              "w-[calc(100%-0.5rem)] flex items-center gap-1.5 px-3 py-1.5 rounded-md mx-1 text-[13px] transition-colors",
              !selection.section ? "bg-accent text-accent-foreground" : "text-foreground/80 hover:bg-muted",
            )}
          >
            <RiStackLine size={14} className="text-muted-foreground" />
            <span className="flex-1 text-left">All flows</span>
            <span className="text-muted-foreground/60 text-[11px]">{flows.length}</span>
          </button>

          {sections.map(({ key, label, team }) => {
            const group = bySection.get(key)!;
            const total = group.ungrouped.length + [...group.folders.values()].reduce((n, f) => n + f.length, 0);
            if (groupBy === "team" && !team && total === 0) return null;
            const isOpen = !collapsed.has(key);
            const isSelected = selection.section === key && !selection.folder;
            return (
              <div key={key} className="mt-1" data-testid={`flow-section-${key}`}>
                <div className="flex items-center px-2">
                  <button onClick={() => toggle(collapsed, key, setCollapsed)} className="p-1 text-muted-foreground hover:text-foreground">
                    <RiArrowRightSLine size={12} className={cn("transition-transform duration-150", isOpen && "rotate-90")} />
                  </button>
                  <button
                    onClick={() => onSelect({ section: key, folder: null })}
                    className={cn(
                      "flex-1 flex items-center gap-1.5 py-1 px-1 rounded text-[11px] font-semibold uppercase tracking-wider transition-colors min-w-0",
                      isSelected ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {groupBy === "team" && <TeamSwatch team={team} />}
                    <span className="truncate">{label}</span>
                    <span className="text-muted-foreground/50 font-normal normal-case">({total})</span>
                  </button>
                </div>
                {isOpen && (
                  <div className="ml-2">
                    {total === 0 && <div className="px-4 py-1.5 text-xs text-muted-foreground">No flows yet</div>}
                    {[...group.folders.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([folder, folderFlows]) => {
                      const folderKey = `${key}:${folder}`;
                      const folderOpen = openFolders.has(folderKey);
                      const folderSelected = selection.section === key && selection.folder === folder;
                      return (
                        <div key={folderKey}>
                          <button
                            onClick={() => {
                              toggle(openFolders, folderKey, setOpenFolders);
                              onSelect({ section: key, folder });
                            }}
                            className={cn(
                              "w-[calc(100%-0.5rem)] flex items-center gap-1.5 px-3 py-1.5 rounded-md mx-1 text-[12px] transition-colors",
                              folderSelected ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground hover:bg-muted",
                            )}
                          >
                            {folderOpen ? (
                              <RiFolderOpenLine size={14} className="flex-shrink-0 text-amber-500" />
                            ) : (
                              <RiFolderLine size={14} className="flex-shrink-0 text-amber-500" />
                            )}
                            <span className="truncate font-medium">{folder}</span>
                            <span className="text-muted-foreground/50 text-[11px] ml-auto">{folderFlows.length}</span>
                          </button>
                          {folderOpen && <div className="ml-3">{folderFlows.map(renderFlowRow)}</div>}
                        </div>
                      );
                    })}
                    {group.ungrouped.map(renderFlowRow)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
