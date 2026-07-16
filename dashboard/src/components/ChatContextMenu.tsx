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
  RiCheckboxLine,
  RiAddLine,
  RiDeleteBinLine,
  RiLoader4Line,
  RiShareLine,
  RiFileCopyLine,
} from "@remixicon/react";
import { basePath, setConversationShared, updateTask } from "@/lib/api";

interface ChatContextMenuProps {
  conversationId: string;
  conversationTitle: string;
  isPinned: boolean;
  projectId?: string | null;
  /** Board membership — pass conversation.task_status to enable add/remove */
  taskStatus?: "todo" | "active" | "done" | null;
  projects: Array<{ project_id: string; name: string }>;
  onRename: (conversationId: string, newTitle: string) => Promise<void>;
  onDelete: (conversationId: string) => Promise<void>;
  onTogglePin: (conversationId: string) => Promise<void>;
  onAssignProject: (conversationId: string, projectId: string) => Promise<void>;
  onRemoveProject: (conversationId: string) => Promise<void>;
  onCreateProject: (name: string) => Promise<void>;
  canShare?: boolean;
  isShared?: boolean;
  onSharingChange?: (shared: boolean) => void;
  triggerClassName?: string;
}

export default function ChatContextMenu({
  conversationId,
  conversationTitle,
  isPinned,
  projectId,
  taskStatus,
  projects,
  onRename,
  onDelete,
  onTogglePin,
  onAssignProject,
  onRemoveProject,
  onCreateProject,
  canShare = false,
  isShared = false,
  onSharingChange,
  triggerClassName,
}: ChatContextMenuProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [shared, setShared] = useState(isShared);
  const [copied, setCopied] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [renameValue, setRenameValue] = useState(conversationTitle);
  const [newProjectName, setNewProjectName] = useState("");
  const [loading, setLoading] = useState(false);
  // Optimistic board membership — lists refresh on their own poll cycles.
  const [onBoard, setOnBoard] = useState(!!taskStatus);

  useEffect(() => {
    setOnBoard(!!taskStatus);
  }, [taskStatus]);

  useEffect(() => setShared(isShared), [isShared]);

  const shareUrl = typeof window === "undefined"
    ? ""
    : `${window.location.origin}${basePath}/chat?continue=${conversationId}`;

  async function handleShare() {
    setLoading(true);
    try {
      await setConversationShared(conversationId, true);
      setShared(true);
      onSharingChange?.(true);
      setShareDialogOpen(true);
    } catch (e) {
      console.error("Failed to share conversation:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleStopSharing() {
    setLoading(true);
    try {
      await setConversationShared(conversationId, false);
      setShared(false);
      onSharingChange?.(false);
      setShareDialogOpen(false);
    } catch (e) {
      console.error("Failed to stop sharing:", e);
    } finally {
      setLoading(false);
    }
  }

  async function copyShareLink() {
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  async function handleToggleBoard() {
    const next = !onBoard;
    setOnBoard(next);
    try {
      await updateTask(conversationId, { task_status: next ? "active" : null });
    } catch (e) {
      setOnBoard(!next);
      console.error("Failed to update board membership:", e);
    }
  }
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

          {canShare && (
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                setDropdownOpen(false);
                if (shared) setShareDialogOpen(true);
                else handleShare();
              }}
            >
              <RiShareLine size={16} className="text-muted-foreground" />
              {shared ? "Manage sharing" : "Share conversation"}
            </DropdownMenuItem>
          )}

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

          {/* Tasks board */}
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation();
              handleToggleBoard();
              setDropdownOpen(false);
            }}
          >
            {onBoard ? (
              <>
                <RiCheckboxLine size={16} className="text-brand-600" />
                Remove from board
              </>
            ) : (
              <>
                <RiCheckboxLine size={16} className="text-muted-foreground" />
                Add to board
              </>
            )}
          </DropdownMenuItem>

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

      <Dialog open={shareDialogOpen} onOpenChange={setShareDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Share conversation</DialogTitle>
            <DialogDescription>
              Anyone on your team with access to Loma can open this conversation and continue the chat.
            </DialogDescription>
          </DialogHeader>
          <div className="flex gap-2">
            <Input value={shareUrl} readOnly aria-label="Conversation share link" />
            <Button onClick={copyShareLink} disabled={!shareUrl}>
              {copied ? <RiCheckLine size={16} /> : <RiFileCopyLine size={16} />}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={handleStopSharing} disabled={loading}>
              Stop sharing
            </Button>
            <Button onClick={() => setShareDialogOpen(false)}>Done</Button>
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
