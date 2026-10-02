"use client";

import { useEffect, useState } from "react";
import {
  RiAddLine,
  RiArrowDownSLine,
  RiBookmarkLine,
  RiCheckLine,
  RiDeleteBinLine,
  RiFileCopyLine,
  RiLinkM,
  RiPencilLine,
  RiTeamLine,
} from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import type { BoardView } from "@/lib/api";

/** View switcher for a card board: "All cards" plus saved views. */
export function BoardViewMenu({
  views, active, dirty, canSave, compact = false,
  onSelect, onSaveNew, onRename, onDuplicate, onCopyLink, onDelete,
}: {
  views: BoardView[];
  active: BoardView | null;
  /** Filters changed since the active view (or "All cards") was opened. */
  dirty: boolean;
  /** Something to save: at least one filter, a search or "Assigned to me". */
  canSave: boolean;
  /** Phones: icon-only trigger. */
  compact?: boolean;
  onSelect: (view: BoardView | null) => void;
  onSaveNew: () => void;
  onRename: (view: BoardView) => void;
  onDuplicate: (view: BoardView) => void;
  onCopyLink: (view: BoardView) => void;
  onDelete: (view: BoardView) => void;
}) {
  const [query, setQuery] = useState("");
  const shown = views.filter((v) => v.name.toLowerCase().includes(query.trim().toLowerCase()));
  const name = active?.name ?? "All cards";
  const unsavedDot = dirty && active && (
    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-label="Unsaved changes" />
  );

  return (
    <DropdownMenu onOpenChange={(open) => { if (!open) setQuery(""); }}>
      <DropdownMenuTrigger asChild>
        {compact ? (
          <Button variant={active ? "secondary" : "ghost"} size="icon"
            className="relative size-10 rounded-full text-muted-foreground" aria-label={`View: ${name}`}>
            <RiBookmarkLine size={18} />
            {unsavedDot && <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-amber-500" />}
          </Button>
        ) : (
          <Button variant={active ? "secondary" : "ghost"} size="sm" className="h-9 max-w-52" aria-label={`View: ${name}`}>
            <RiBookmarkLine className="h-4 w-4" />
            <span className="truncate">{name}</span>
            {unsavedDot}
            <RiArrowDownSLine className="h-4 w-4 text-muted-foreground" />
          </Button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        {views.length > 6 && (
          <Input autoFocus value={query} placeholder="Search views" aria-label="Search views"
            className="mb-1 h-8 text-[13px]"
            // Keep typing in the box instead of jumping between menu items.
            onKeyDown={(e) => e.stopPropagation()}
            onChange={(e) => setQuery(e.target.value)} />
        )}
        <DropdownMenuItem onSelect={() => onSelect(null)}>
          <span className="flex-1">All cards</span>
          {!active && <RiCheckLine className="h-3.5 w-3.5" />}
        </DropdownMenuItem>
        {shown.map((view) => (
          <DropdownMenuItem key={view.id} onSelect={() => onSelect(view)}>
            <span className="min-w-0 flex-1 truncate">{view.name}</span>
            {view.shared && (
              <RiTeamLine className="h-3.5 w-3.5 text-muted-foreground" aria-label="Shared with the board" />
            )}
            {view.id === active?.id && <RiCheckLine className="h-3.5 w-3.5" />}
          </DropdownMenuItem>
        ))}
        {active && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="truncate">{active.name}</DropdownMenuLabel>
            {active.can_edit && (
              <DropdownMenuItem onSelect={() => onRename(active)}><RiPencilLine /> Rename</DropdownMenuItem>
            )}
            <DropdownMenuItem onSelect={() => onDuplicate(active)}><RiFileCopyLine /> Duplicate</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onCopyLink(active)}><RiLinkM /> Copy link</DropdownMenuItem>
            {active.can_edit && (
              <DropdownMenuItem variant="destructive" onSelect={() => onDelete(active)}>
                <RiDeleteBinLine /> Delete
              </DropdownMenuItem>
            )}
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={!canSave} onSelect={onSaveNew}>
          <RiAddLine /> Save current as new view
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Name (and share) a view: used to save a new view and to rename one. */
export function SaveViewDialog({ open, onOpenChange, title, initialName, initialShared, canShare, summary, onSubmit }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  initialName: string;
  initialShared: boolean;
  /** Owners and editors can share a view with the board. */
  canShare: boolean;
  /** What the view saves, e.g. "2 filters · search “bank”". */
  summary?: string;
  onSubmit: (name: string, shared: boolean) => Promise<void>;
}) {
  const [name, setName] = useState(initialName);
  const [shared, setShared] = useState(initialShared);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(initialName);
    setShared(initialShared);
    setError(null);
  }, [open, initialName, initialShared]);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setSaving(true);
    setError(null);
    try {
      await onSubmit(trimmed, shared);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save view");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {summary && <DialogDescription>Saves {summary}</DialogDescription>}
        </DialogHeader>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <div className="space-y-1.5">
            <Label htmlFor="view-name">Name</Label>
            <Input id="view-name" autoFocus maxLength={60} value={name} placeholder="Big deals"
              onChange={(e) => setName(e.target.value)} />
          </div>
          {canShare && (
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="view-shared" className="font-normal">Share with everyone on this board</Label>
              <Switch id="view-shared" checked={shared} onCheckedChange={setShared} />
            </div>
          )}
          {error && <p className={cn("text-xs text-destructive")}>{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={saving || !name.trim()}>{saving ? "Saving..." : "Save view"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
