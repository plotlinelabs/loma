"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  RiAddLine,
  RiChat1Line,
  RiCheckLine,
  RiDeleteBinLine,
  RiFileCopyLine,
  RiFolderAddLine,
  RiFolderLine,
  RiFolderTransferLine,
  RiLoader4Line,
  RiMoreLine,
  RiPencilLine,
  RiShareLine,
} from "@remixicon/react";
import {
  basePath,
  buildProjectTree,
  deleteProject,
  fetchProject,
  flattenProjectTree,
  setProjectShared,
  updateProject,
} from "../../../lib/api";
import type { Conversation, Project } from "../../../lib/api";
import { useUser } from "../../../lib/UserContext";
import ChatContextMenu from "../../../components/ChatContextMenu";
import ClientTimestamp from "../../../components/ClientTimestamp";
import { EmptyState } from "../../../components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type FolderData = Awaited<ReturnType<typeof fetchProject>>;
type DialogKind = "subfolder" | "rename" | "move" | "share" | "delete" | null;

export default function FolderPage() {
  const id = useParams().id as string;
  const router = useRouter();
  const { projects, isPinned, togglePin, renameConversation, removeConversation, assignToProject, unassignFromProject, addProject, refreshProjects } = useUser();
  const [data, setData] = useState<FolderData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [nameValue, setNameValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    fetchProject(id)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch(() => setError("Folder not found"));
  }, [id]);

  useEffect(() => {
    setData(null);
    load();
  }, [load]);

  // Valid move targets: every own folder except this one and anything nested inside it.
  const moveTargets = useMemo(() => {
    const flat = flattenProjectTree(buildProjectTree(projects));
    const self = flat.find((p) => p.project_id === id);
    const blocked = new Set(self ? [id, ...flattenProjectTree(self.children).map((p) => p.project_id)] : [id]);
    return flat.filter((p) => !blocked.has(p.project_id));
  }, [projects, id]);

  if (error) {
    return (
      <Alert variant="destructive" className="text-center py-20 rounded-xl">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!data) {
    return (
      <div className="flex items-center justify-center py-20 gap-2 text-muted-foreground text-[13px]">
        <RiLoader4Line size={14} className="animate-spin text-brand-600" />
        Loading folder...
      </div>
    );
  }

  const { project, breadcrumbs, subfolders, conversations, can_manage: canManage } = data;
  const isShared = project.visibility === "shared";
  const shareUrl = typeof window === "undefined" ? "" : `${window.location.origin}${basePath}/projects/${id}`;

  function open(kind: DialogKind, name = "") {
    setActionError(null);
    setNameValue(name);
    setDialog(kind);
  }

  /** Run a folder mutation, then refresh this page and the sidebar tree. */
  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      refreshProjects();
      if (after) after();
      else {
        setDialog(null);
        load();
      }
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const submitName = () => {
    const name = nameValue.trim();
    if (!name) return;
    if (dialog === "subfolder") run(() => addProject(name, id));
    else run(() => updateProject(id, { name }));
  };

  const chatMenu = (c: Conversation) => (
    <ChatContextMenu
      conversationId={c.conversation_id}
      conversationTitle={c.title || c.prompt?.slice(0, 50) || "Untitled"}
      isPinned={isPinned(c.conversation_id)}
      projectId={c.project_id}
      taskStatus={c.task_status}
      projects={projects}
      onRename={async (cid, title) => { await renameConversation(cid, title); load(); }}
      onDelete={async (cid) => { await removeConversation(cid); load(); }}
      onTogglePin={togglePin}
      onAssignProject={async (cid, pid) => { await assignToProject(cid, pid); load(); }}
      onRemoveProject={async (cid) => { await unassignFromProject(cid); load(); }}
      onCreateProject={async (name) => { await addProject(name); }}
    />
  );

  return (
    <div className="space-y-4">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>Folders</BreadcrumbItem>
          {breadcrumbs.map((b: Project) => (
            <span key={b.project_id} className="contents">
              <BreadcrumbSeparator />
              <BreadcrumbItem>
                <BreadcrumbLink href={`${basePath}/projects/${b.project_id}`}>{b.name}</BreadcrumbLink>
              </BreadcrumbItem>
            </span>
          ))}
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{project.name}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <RiFolderLine size={20} className="flex-shrink-0 text-muted-foreground" style={{ color: project.color || undefined }} />
          <h1 className="truncate text-lg font-semibold text-foreground">{project.name}</h1>
          {data.shared && <Badge variant="secondary">{canManage ? "Shared" : `Shared by ${project.created_by}`}</Badge>}
        </div>
        {canManage && (
          <div className="flex items-center gap-1.5">
            <Button size="sm" asChild>
              <Link href={`/chat?project=${id}`}>
                <RiAddLine size={14} />
                New chat
              </Link>
            </Button>
            <Button size="sm" variant="outline" onClick={() => open("subfolder")}>
              <RiFolderAddLine size={14} />
              New sub-folder
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => (isShared ? open("share") : run(() => setProjectShared(id, true), () => { load(); open("share"); }))}
            >
              <RiShareLine size={14} />
              {isShared ? "Manage sharing" : "Share"}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="icon-sm" variant="ghost" title="Folder actions" aria-label="Folder actions">
                  <RiMoreLine size={16} />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuItem onClick={() => open("rename", project.name)}>
                  <RiPencilLine size={16} className="text-muted-foreground" />
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => open("move")}>
                  <RiFolderTransferLine size={16} className="text-muted-foreground" />
                  Move to...
                </DropdownMenuItem>
                <DropdownMenuItem variant="destructive" onClick={() => open("delete")}>
                  <RiDeleteBinLine size={16} />
                  Delete folder
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </div>

      {subfolders.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {subfolders.map((f: Project) => (
            <Link
              key={f.project_id}
              href={`/projects/${f.project_id}`}
              className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2.5 text-[13px] font-medium text-foreground hover:bg-muted transition-colors"
            >
              <RiFolderLine size={16} className="flex-shrink-0 text-muted-foreground" style={{ color: f.color || undefined }} />
              <span className="truncate">{f.name}</span>
            </Link>
          ))}
        </div>
      )}

      {conversations.length === 0 ? (
        <EmptyState
          icon={RiChat1Line}
          title="No chats in this folder yet"
          description={canManage ? "Start a new chat here, or move an existing chat in from its menu." : undefined}
        />
      ) : (
        <div className="rounded-lg border border-border bg-card divide-y divide-border">
          {conversations.map((c: Conversation) => (
            <div key={c.conversation_id} className="group flex items-center gap-2 px-3 py-2 hover:bg-muted/60">
              <RiChat1Line size={14} className="flex-shrink-0 text-muted-foreground" />
              {/* Owners continue the chat; viewers of a shared folder get the read-only transcript. */}
              <Link
                href={canManage ? `/chat?continue=${c.conversation_id}` : `/conversations/${c.conversation_id}`}
                className="flex-1 min-w-0 truncate text-[13px] text-foreground"
              >
                {c.title || c.prompt?.slice(0, 80) || "Untitled"}
              </Link>
              <span className="text-xs text-muted-foreground whitespace-nowrap">
                <ClientTimestamp iso={c.started_at} variant="short" />
              </span>
              {canManage && chatMenu(c)}
            </div>
          ))}
        </div>
      )}

      {/* New sub-folder / rename */}
      <Dialog open={dialog === "subfolder" || dialog === "rename"} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{dialog === "rename" ? "Rename folder" : `New folder in "${project.name}"`}</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={nameValue}
            onChange={(e) => setNameValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitName()}
            maxLength={100}
            placeholder="Folder name..."
          />
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>Cancel</Button>
            <Button onClick={submitName} disabled={busy || !nameValue.trim()}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Move */}
      <Dialog open={dialog === "move"} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Move folder</DialogTitle>
            <DialogDescription>Its sub-folders and chats move with it.</DialogDescription>
          </DialogHeader>
          <div className="max-h-72 overflow-y-auto space-y-px">
            {[{ project_id: null, name: "Top level", depth: 0 }, ...moveTargets].map((t) => {
              const current = (project.parent_id || null) === t.project_id;
              return (
                <button
                  key={t.project_id || "root"}
                  type="button"
                  disabled={busy || current}
                  onClick={() => run(() => updateProject(id, { parent_id: t.project_id }))}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-muted disabled:opacity-60",
                    current && "font-medium",
                  )}
                  style={{ paddingLeft: 8 + Math.min(t.depth, 8) * 14 }}
                >
                  <RiFolderLine size={14} className="flex-shrink-0 text-muted-foreground" />
                  <span className="truncate">{t.name}</span>
                  {current && <RiCheckLine size={14} className="ml-auto flex-shrink-0" />}
                </button>
              );
            })}
          </div>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
        </DialogContent>
      </Dialog>

      {/* Share — same link-sharing model as conversations, but read-only */}
      <Dialog open={dialog === "share"} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Share folder</DialogTitle>
            <DialogDescription>
              Anyone on your team with access to Loma can open this link and read every chat in this folder and its
              sub-folders, including chats added later. They cannot edit the folder or continue the chats.
            </DialogDescription>
          </DialogHeader>
          <div className="flex gap-2">
            <Input value={shareUrl} readOnly aria-label="Folder share link" />
            <Button
              onClick={async () => {
                await navigator.clipboard.writeText(shareUrl);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
            >
              {copied ? <RiCheckLine size={16} /> : <RiFileCopyLine size={16} />}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => run(() => setProjectShared(id, false))}>
              Stop sharing
            </Button>
            <Button onClick={() => setDialog(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <Dialog open={dialog === "delete"} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete &quot;{project.name}&quot;?</DialogTitle>
            <DialogDescription>
              This also deletes its sub-folders. Chats are not deleted; they move back to your main chat list.
            </DialogDescription>
          </DialogHeader>
          {actionError && <p className="text-xs text-destructive">{actionError}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(null)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() =>
                run(() => deleteProject(id), () =>
                  router.push(project.parent_id ? `/projects/${project.parent_id}` : "/"))
              }
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
