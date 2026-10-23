"use client";

import { useState } from "react";
import type { Flow } from "../../../lib/api";
import { organizeFlow } from "../../../lib/api";
import type { TeamDirectoryEntry } from "../../../lib/governance-api";
import { TeamSwatch } from "../../../components/TeamGrouping";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { RiAddLine, RiCheckLine, RiCloseLine, RiFolderLine, RiMoreLine, RiTeamLine } from "@remixicon/react";

/** "…" menu to move a flow between teams and folders (mirrors the skills tree menu). */
export default function FlowOrganizeMenu({
  flow,
  teams,
  folders,
  onChanged,
  triggerClassName,
}: {
  flow: Flow;
  teams: TeamDirectoryEntry[];
  folders: string[];
  onChanged: (flow: Flow) => void;
  triggerClassName?: string;
}) {
  const [newFolder, setNewFolder] = useState<string | null>(null);

  async function apply(updates: { team_id?: string | null; folder?: string | null }) {
    try {
      const { flow: updated } = await organizeFlow(flow.flow_id, updates);
      onChanged(updated);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to organize flow");
    }
  }

  function submitFolder() {
    const name = (newFolder || "").trim();
    if (name) apply({ folder: name });
    setNewFolder(null);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          onClick={(e) => e.stopPropagation()}
          className={triggerClassName ?? "p-0.5 rounded flex-shrink-0 text-muted-foreground hover:text-foreground"}
          data-testid={`flow-organize-${flow.flow_id}`}
          aria-label="Organize flow"
        >
          <RiMoreLine size={14} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[180px]">
        <DropdownMenuSub>
          <DropdownMenuSubTrigger data-testid="flow-assign-team">
            <RiTeamLine size={14} className="text-muted-foreground" />
            {flow.team_id ? "Change team" : "Assign to team"}
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-48">
            {flow.team_id && (
              <DropdownMenuItem onClick={() => apply({ team_id: null })}>
                <RiCloseLine size={14} />
                Remove from team
              </DropdownMenuItem>
            )}
            {teams.length === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">No teams yet. Admins can create them in Admin → Teams.</div>
            )}
            {teams.map((t) => (
              <DropdownMenuItem
                key={t.team_id}
                onClick={() => apply({ team_id: t.team_id })}
                className={t.team_id === flow.team_id ? "text-brand-600 font-medium" : ""}
              >
                <TeamSwatch team={t} />
                <span className="truncate">{t.name}</span>
                {t.team_id === flow.team_id && <RiCheckLine size={14} className="text-brand-600 ml-auto flex-shrink-0" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <RiFolderLine size={14} className="text-muted-foreground" />
            {flow.folder ? "Change folder" : "Move to folder"}
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-48">
            {flow.folder && (
              <DropdownMenuItem onClick={() => apply({ folder: null })}>
                <RiCloseLine size={14} />
                Remove from folder
              </DropdownMenuItem>
            )}
            {folders.map((f) => (
              <DropdownMenuItem
                key={f}
                onClick={() => apply({ folder: f })}
                className={f === flow.folder ? "text-brand-600 font-medium" : ""}
              >
                <RiFolderLine size={14} className="text-muted-foreground" />
                <span className="truncate">{f}</span>
                {f === flow.folder && <RiCheckLine size={14} className="text-brand-600 ml-auto flex-shrink-0" />}
              </DropdownMenuItem>
            ))}
            {newFolder !== null ? (
              <div className="px-2 py-1.5">
                <Input
                  autoFocus
                  value={newFolder}
                  onChange={(e) => setNewFolder(e.target.value)}
                  onKeyDown={(e) => {
                    e.stopPropagation();
                    if (e.key === "Enter") submitFolder();
                    if (e.key === "Escape") setNewFolder(null);
                  }}
                  placeholder="Folder name..."
                  className="h-7 text-xs"
                  data-testid="flow-new-folder-input"
                />
              </div>
            ) : (
              <DropdownMenuItem onClick={(e) => { e.preventDefault(); setNewFolder(""); }}>
                <RiAddLine size={14} className="text-muted-foreground" />
                New folder
              </DropdownMenuItem>
            )}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
