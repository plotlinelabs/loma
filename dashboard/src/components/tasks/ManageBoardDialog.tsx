"use client";

import { useEffect, useState } from "react";
import { RiCloseLine, RiDeleteBinLine } from "@remixicon/react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  type BoardTemplate,
  createTaskBoard,
  deleteTaskBoard,
  updateTaskBoard,
  type TaskBoardMember,
  type TaskBoardSummary,
} from "@/lib/api";

type MemberRole = TaskBoardMember["role"];

interface ManageBoardDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Board to manage; null creates a new board. */
  board: TaskBoardSummary | null;
  onSaved: (board: TaskBoardSummary) => void;
  onDeleted?: () => void;
}

const TEMPLATES: Array<{ id: BoardTemplate; label: string; hint: string }> = [
  { id: "blank", label: "Blank", hint: "No fields yet" },
  { id: "deals", label: "Deals", hint: "Value, Owner, Close date" },
  { id: "hiring", label: "Hiring", hint: "Role, Source, Interview date" },
  { id: "projects", label: "Projects", hint: "Owner, Due date" },
];

function RoleSelect({ value, onChange }: { value: MemberRole; onChange: (role: MemberRole) => void }) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as MemberRole)}>
      <SelectTrigger size="sm" className="w-24 shrink-0" aria-label="Access">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="owner">Owner</SelectItem>
        <SelectItem value="editor">Editor</SelectItem>
        <SelectItem value="viewer">Viewer</SelectItem>
      </SelectContent>
    </Select>
  );
}

/** Create a board, or rename / share / delete one you own. */
export function ManageBoardDialog({ open, onOpenChange, board, onSaved, onDeleted }: ManageBoardDialogProps) {
  const [name, setName] = useState("");
  const [members, setMembers] = useState<TaskBoardMember[]>([]);
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<MemberRole>("editor");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // New boards only: cards that hold tasks, started from a template.
  const [cardMode, setCardMode] = useState(false);
  const [template, setTemplate] = useState<BoardTemplate>("blank");

  useEffect(() => {
    if (!open) return;
    setCardMode(false);
    setTemplate("blank");
    setName(board?.name ?? "");
    setMembers(board?.members ?? []);
    setNewEmail("");
    setNewRole("editor");
    setError(null);
  }, [open, board]);

  const addMember = () => {
    const email = newEmail.trim().toLowerCase();
    if (!email) return;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      setError("Enter a valid email");
      return;
    }
    if (email === board?.owner || members.some((m) => m.email === email)) {
      setError(`${email} already has access`);
      return;
    }
    setMembers([...members, { email, role: newRole }]);
    setNewEmail("");
    setError(null);
  };

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Give the board a name");
      return;
    }
    // Typed but not added yet: include it rather than silently dropping it.
    const pending = newEmail.trim().toLowerCase();
    const nextMembers = pending && !members.some((m) => m.email === pending)
      ? [...members, { email: pending, role: newRole }]
      : members;
    setBusy(true);
    setError(null);
    try {
      let saved = board;
      if (!saved) {
        saved = (await createTaskBoard(trimmed, cardMode ? { card_mode: true, template } : {})).board;
      }
      const changes: { name?: string; members?: TaskBoardMember[] } = {};
      if (board && trimmed !== board.name) changes.name = trimmed;
      if (nextMembers.length > 0 || (board?.members.length ?? 0) > 0) changes.members = nextMembers;
      if (Object.keys(changes).length > 0) saved = (await updateTaskBoard(saved.id, changes)).board;
      onSaved(saved);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save board");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!board) return;
    if (!window.confirm(`Delete "${board.name}"? Its tasks go back to the boards of the people who created them.`)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteTaskBoard(board.id);
      onOpenChange(false);
      onDeleted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete board");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{board ? "Share board" : "New board"}</DialogTitle>
          <DialogDescription>
            Each board has its own columns, tags and context. Owners and editors can message, edit and move any task; viewers only read. A task runs with the accounts of whoever sends it a message.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="board-name">Name</Label>
            <Input
              id="board-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Deals"
              maxLength={60}
              autoFocus={!board}
            />
          </div>
          {!board && (
            <div className="space-y-2 rounded-lg border border-border p-3">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <Label htmlFor="board-card-mode">Cards hold tasks inside them</Label>
                  <p className="text-xs text-muted-foreground">
                    For tracking things like deals or candidates: each card has its own fields, notes and tasks.
                    Off = every card is just a task. This can&apos;t be changed later.
                  </p>
                </div>
                <Switch id="board-card-mode" checked={cardMode} onCheckedChange={setCardMode} />
              </div>
              {cardMode && (
                <div className="flex items-center gap-2">
                  <Label htmlFor="board-template" className="shrink-0 text-xs text-muted-foreground">Start from</Label>
                  <Select value={template} onValueChange={(v) => setTemplate(v as BoardTemplate)}>
                    <SelectTrigger id="board-template" size="sm" className="flex-1"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {TEMPLATES.map((t) => (
                        <SelectItem key={t.id} value={t.id}>
                          {t.label} <span className="text-muted-foreground">· {t.hint}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="board-member-email">People with access</Label>
            <p className="text-xs text-muted-foreground">
              Owners can also share, rename and delete the board. Editors can add, move and assign tasks and
              change columns, tags and context. Viewers can only look.
            </p>
            <div className="flex items-center gap-1.5">
              <Input
                id="board-member-email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addMember();
                  }
                }}
                placeholder="name@company.com"
                className="h-8"
              />
              <RoleSelect value={newRole} onChange={setNewRole} />
              <Button variant="secondary" size="sm" onClick={addMember} disabled={!newEmail.trim()}>
                Add
              </Button>
            </div>
            <div className="divide-y divide-border rounded-lg border border-border">
              <div className="flex items-center gap-2 px-3 py-2 text-[13px]">
                <span className="min-w-0 flex-1 truncate">{board?.owner ?? "You"}</span>
                <span className="text-xs text-muted-foreground">Owner (creator)</span>
              </div>
              {members.map((member) => (
                <div key={member.email} className="flex items-center gap-2 px-3 py-1.5 text-[13px]">
                  <span className="min-w-0 flex-1 truncate">{member.email}</span>
                  <RoleSelect
                    value={member.role}
                    onChange={(role) => setMembers(members.map((m) => (m.email === member.email ? { ...m, role } : m)))}
                  />
                  <Button
                    variant="ghost" size="icon" className="h-7 w-7 shrink-0 text-muted-foreground"
                    onClick={() => setMembers(members.filter((m) => m.email !== member.email))}
                    aria-label={`Remove ${member.email}`}
                  >
                    <RiCloseLine className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter className="sm:justify-between">
          {board ? (
            <Button variant="ghost" className="text-destructive" onClick={() => void remove()} disabled={busy}>
              <RiDeleteBinLine className="h-4 w-4" /> Delete board
            </Button>
          ) : <span />}
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
            <Button onClick={() => void save()} disabled={busy}>{board ? "Save" : "Create board"}</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
