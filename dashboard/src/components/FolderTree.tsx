"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { RiAddLine, RiArrowRightSLine, RiFolderLine, RiFolderOpenLine, RiShareLine } from "@remixicon/react";
import { buildProjectTree, updateProject } from "../lib/api";
import type { Project, ProjectTreeNode } from "../lib/api";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Drag payloads: a chat row dropped on a folder files it there; a folder dropped on a folder nests it. */
export const CHAT_DRAG_TYPE = "application/x-loma-chat";
const FOLDER_DRAG_TYPE = "application/x-loma-folder";
const EXPANDED_KEY = "loma.folders.expanded";

function loadExpanded(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(EXPANDED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

export default function FolderTree({
  projects,
  onNavigate,
  onCreate,
  onAssignChat,
  onChanged,
}: {
  projects: Project[];
  onNavigate: () => void;
  onCreate: (name: string) => Promise<unknown>;
  onAssignChat: (conversationId: string, projectId: string) => Promise<void>;
  /** Called after a folder was moved so the caller can refresh the list */
  onChanged: () => void;
}) {
  const pathname = usePathname();
  const activeId = pathname.startsWith("/projects/") ? pathname.split("/")[2] : null;
  const tree = useMemo(() => buildProjectTree(projects), [projects]);
  // The sidebar only mounts client-side (after the session loads), so reading localStorage here is safe.
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    typeof window === "undefined" ? new Set() : loadExpanded(),
  );
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  // Ancestors of the folder being viewed always stay open, so the path down to it is visible.
  const activePath = useMemo(() => {
    const byId = new Map(projects.map((p) => [p.project_id, p]));
    const path = new Set<string>();
    let parent = activeId ? byId.get(activeId)?.parent_id : null;
    while (parent && byId.has(parent) && !path.has(parent)) {
      path.add(parent);
      parent = byId.get(parent)?.parent_id;
    }
    return path;
  }, [activeId, projects]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      localStorage.setItem(EXPANDED_KEY, JSON.stringify([...next]));
      return next;
    });
  }

  async function submitNew() {
    const name = newName.trim();
    setCreating(false);
    setNewName("");
    if (name) await onCreate(name).catch((e) => console.error("Failed to create folder:", e));
  }

  async function handleDrop(e: React.DragEvent, targetId: string) {
    e.preventDefault();
    setDropTarget(null);
    const chatId = e.dataTransfer.getData(CHAT_DRAG_TYPE);
    const folderId = e.dataTransfer.getData(FOLDER_DRAG_TYPE);
    try {
      if (chatId) {
        await onAssignChat(chatId, targetId);
      } else if (folderId && folderId !== targetId) {
        // The API rejects moving a folder into its own sub-tree.
        await updateProject(folderId, { parent_id: targetId });
        setExpanded((prev) => new Set([...prev, targetId]));
        onChanged();
      }
    } catch (err) {
      console.error("Failed to move into folder:", err);
    }
  }

  function renderNode(node: ProjectTreeNode) {
    const isOpen = expanded.has(node.project_id) || activePath.has(node.project_id);
    const isActive = activeId === node.project_id;
    return (
      <div key={node.project_id}>
        <div
          draggable
          onDragStart={(e) => e.dataTransfer.setData(FOLDER_DRAG_TYPE, node.project_id)}
          onDragOver={(e) => {
            e.preventDefault();
            setDropTarget(node.project_id);
          }}
          onDragLeave={() => setDropTarget((t) => (t === node.project_id ? null : t))}
          onDrop={(e) => handleDrop(e, node.project_id)}
          className={cn(
            "group flex items-center gap-1 pr-2 py-1 text-[13px] max-md:py-2 max-md:text-[15px] rounded-lg transition-all duration-150",
            isActive
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/75 hover:text-sidebar-accent-foreground hover:bg-sidebar-accent/60",
            dropTarget === node.project_id && "ring-1 ring-sidebar-primary bg-sidebar-accent/60",
          )}
          // Indent by depth; capped so very deep trees stay readable in a narrow sidebar.
          style={{ paddingLeft: 4 + Math.min(node.depth, 8) * 12 }}
        >
          <button
            type="button"
            onClick={() => toggle(node.project_id)}
            aria-label={isOpen ? "Collapse folder" : "Expand folder"}
            className={cn("flex-shrink-0 rounded p-0.5 hover:bg-sidebar-accent", !node.children.length && "invisible")}
          >
            <RiArrowRightSLine size={14} className={cn("transition-transform", isOpen && "rotate-90")} />
          </button>
          <Link
            href={`/projects/${node.project_id}`}
            prefetch
            onClick={onNavigate}
            title={node.name}
            className="flex items-center gap-1.5 flex-1 min-w-0"
          >
            {isOpen && node.children.length ? (
              <RiFolderOpenLine size={14} className="flex-shrink-0" style={{ color: node.color || undefined }} />
            ) : (
              <RiFolderLine size={14} className="flex-shrink-0" style={{ color: node.color || undefined }} />
            )}
            <span className="truncate">{node.name}</span>
            {node.visibility === "shared" && <RiShareLine size={11} className="flex-shrink-0 opacity-60" aria-label="Shared" />}
          </Link>
          <span className="text-[10px] text-sidebar-foreground/50 tabular-nums">{node.conversation_count || 0}</span>
        </div>
        {isOpen && node.children.map(renderNode)}
      </div>
    );
  }

  return (
    <div className="mt-6 flex flex-col">
      <div className="px-3 pb-1.5 flex items-center justify-between">
        <span className="text-[10px] max-md:text-xs font-medium text-sidebar-foreground/60 uppercase tracking-[0.14em]">
          Folders
        </span>
        <button
          type="button"
          onClick={() => setCreating(true)}
          title="New folder"
          aria-label="New folder"
          className="rounded p-0.5 text-sidebar-foreground/60 hover:text-sidebar-accent-foreground hover:bg-sidebar-accent/60"
        >
          <RiAddLine size={14} />
        </button>
      </div>
      <div className="px-2 space-y-px">
        {tree.map(renderNode)}
        {creating && (
          <Input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitNew();
              if (e.key === "Escape") {
                setCreating(false);
                setNewName("");
              }
            }}
            onBlur={submitNew}
            maxLength={100}
            className="h-7 text-xs"
            placeholder="Folder name..."
          />
        )}
        {!tree.length && !creating && (
          <p className="px-2 py-1 text-[12px] text-sidebar-foreground/50">Group chats into folders.</p>
        )}
      </div>
    </div>
  );
}
