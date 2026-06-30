"use client";

import { useState, useRef, useEffect } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  RiMoreLine,
  RiPushpinFill,
  RiPushpinLine,
  RiPencilLine,
  RiFolderLine,
  RiCloseLine,
  RiCheckLine,
  RiAddLine,
  RiDeleteBinLine,
  RiLoader4Line,
} from "@remixicon/react";

interface ChatContextMenuProps {
  conversationId: string;
  conversationTitle: string;
  isPinned: boolean;
  projectId?: string | null;
  projects: Array<{ project_id: string; name: string }>;
  onRename: (conversationId: string, newTitle: string) => Promise<void>;
  onDelete: (conversationId: string) => Promise<void>;
  onTogglePin: (conversationId: string) => Promise<void>;
  onAssignProject: (conversationId: string, projectId: string) => Promise<void>;
  onRemoveProject: (conversationId: string) => Promise<void>;
  onCreateProject: (name: string) => Promise<void>;
  triggerClassName?: string;
}

export default function ChatContextMenu({
  conversationId,
  conversationTitle,
  isPinned,
  projectId,
  projects,
  onRename,
  onDelete,
  onTogglePin,
  onAssignProject,
  onRemoveProject,
  onCreateProject,
  triggerClassName,
}: ChatContextMenuProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [renameValue, setRenameValue] = useState(conversationTitle);
  const [newProjectName, setNewProjectName] = useState("");
  const [loading, setLoading] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const projectInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renameDialogOpen) {
      // Small delay to ensure the dialog is mounted
      const timer = setTimeout(() => {
        renameInputRef.current?.focus();
        renameInputRef.current?.select();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [renameDialogOpen]);

  useEffect(() => {
    if (showNewProject) {
      const timer = setTimeout(() => {
        projectInputRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [showNewProject]);

  function closeAll() {
    setDropdownOpen(false);
    setRenameDialogOpen(false);
    setDeleteDialogOpen(false);
    setShowNewProject(false);
    setNewProjectName("");
  }

  async function handleRename() {
    const trimmed = renameValue.trim();
    if (!trimmed || trimmed === conversationTitle) {
      setRenameDialogOpen(false);
      return;
    }
    setLoading(true);
    try {
      await onRename(conversationId, trimmed);
      closeAll();
    } catch (e) {
      console.error("Failed to rename:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete() {
    setLoading(true);
    try {
      await onDelete(conversationId);
      closeAll();
    } catch (e) {
      console.error("Failed to delete:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateProject() {
    const trimmed = newProjectName.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      await onCreateProject(trimmed);
      setShowNewProject(false);
      setNewProjectName("");
    } catch (e) {
      console.error("Failed to create project:", e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <DropdownMenu open={dropdownOpen} onOpenChange={setDropdownOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon-xs"
            className={triggerClassName || "text-gray-400 hover:text-gray-600 hover:bg-gray-100"}
            title="More actions"
            onClick={(e) => {
              e.stopPropagation();
              e.preventDefault();
            }}
          >
            <RiMoreLine size={16} />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          {/* Pin / Unpin */}
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation();
              onTogglePin(conversationId);
              setDropdownOpen(false);
            }}
          >
            {isPinned ? (
              <>
                <RiPushpinFill size={16} className="text-amber-500" />
                Unpin
              </>
            ) : (
              <>
                <RiPushpinLine size={16} className="text-muted-foreground" />
                Pin
              </>
            )}
          </DropdownMenuItem>

          {/* Rename */}
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation();
              setDropdownOpen(false);
              setRenameValue(conversationTitle);
              setRenameDialogOpen(true);
            }}
          >
            <RiPencilLine size={16} className="text-muted-foreground" />
            Rename
          </DropdownMenuItem>

          {/* Add to project */}
          <DropdownMenuSub>
            <DropdownMenuSubTrigger
              onClick={(e) => e.stopPropagation()}
            >
              <RiFolderLine size={16} className="text-muted-foreground" />
              {projectId ? "Move to project" : "Add to project"}
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-48">
              {projectId && (
                <DropdownMenuItem
                  variant="destructive"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemoveProject(conversationId);
                    setDropdownOpen(false);
                  }}
                >
                  <RiCloseLine size={16} />
                  Remove from project
                </DropdownMenuItem>
              )}
              {projects.map((p) => (
                <DropdownMenuItem
                  key={p.project_id}
                  onClick={(e) => {
                    e.stopPropagation();
                    onAssignProject(conversationId, p.project_id);
                    setDropdownOpen(false);
                  }}
                  className={p.project_id === projectId ? "text-brand-600 font-medium" : ""}
                >
                  <span className="w-2 h-2 rounded-full bg-gray-300 flex-shrink-0" />
                  <span className="truncate">{p.name}</span>
                  {p.project_id === projectId && (
                    <RiCheckLine size={14} className="text-brand-600 ml-auto flex-shrink-0" />
                  )}
                </DropdownMenuItem>
              ))}
              {showNewProject ? (
                <div className="px-2 py-1.5">
                  <Input
                    ref={projectInputRef}
                    type="text"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleCreateProject();
                      if (e.key === "Escape") {
                        setShowNewProject(false);
                        setNewProjectName("");
                      }
                    }}
                    maxLength={100}
                    disabled={loading}
                    className="h-7 text-xs"
                    placeholder="Project name..."
                    onClick={(e) => e.stopPropagation()}
                  />
                </div>
              ) : (
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowNewProject(true);
                  }}
                  className="text-brand-600"
                >
                  <RiAddLine size={16} />
                  New project
                </DropdownMenuItem>
              )}
            </DropdownMenuSubContent>
          </DropdownMenuSub>

          <DropdownMenuSeparator />

          {/* Delete */}
          <DropdownMenuItem
            variant="destructive"
            onClick={(e) => {
              e.stopPropagation();
              setDropdownOpen(false);
              setDeleteDialogOpen(true);
            }}
          >
            <RiDeleteBinLine size={16} />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Rename Dialog */}
      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent className="sm:max-w-sm" showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Rename conversation</DialogTitle>
            <DialogDescription>Enter a new title for this conversation.</DialogDescription>
          </DialogHeader>
          <Input
            ref={renameInputRef}
            type="text"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRename();
              if (e.key === "Escape") setRenameDialogOpen(false);
            }}
            maxLength={200}
            disabled={loading}
            placeholder="Enter new title..."
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenameDialogOpen(false)}
              disabled={loading}
            >
              Cancel
            </Button>
            <Button
              onClick={handleRename}
              disabled={loading || !renameValue.trim()}
            >
              {loading ? (
                <>
                  <RiLoader4Line size={16} className="animate-spin" />
                  Saving...
                </>
              ) : (
                "Save"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent className="sm:max-w-sm" showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Delete conversation?</DialogTitle>
            <DialogDescription>This cannot be undone.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
              disabled={loading}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={loading}
            >
              {loading ? (
                <>
                  <RiLoader4Line size={16} className="animate-spin" />
                  Deleting...
                </>
              ) : (
                "Delete"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
