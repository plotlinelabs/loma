"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import ClientTimestamp from "@/components/ClientTimestamp";
import { fetchConversations, updateTask, type Conversation } from "@/lib/api";

const statusDotStyles: Record<string, string> = {
  running: "bg-blue-500 animate-pulse",
  completed: "bg-emerald-500",
  error: "bg-red-500",
  interrupted: "bg-red-500",
};

interface AddChatDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: () => void;
}

/** Pick one of your existing chats and track it on the board. */
export function AddChatDialog({ open, onOpenChange, onAdded }: AddChatDialogProps) {
  const { data: session } = useSession();
  const [search, setSearch] = useState("");
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSearch("");
    setError(null);
    setConversations(null);
  }, [open]);

  // Debounced search over the caller's own conversations.
  useEffect(() => {
    if (!open || !session?.user?.email) return;
    const timer = setTimeout(() => {
      fetchConversations({ person: session.user!.email!, search: search.trim() || undefined })
        .then((data) => setConversations(data.conversations))
        .catch((e) => setError(e instanceof Error ? e.message : "Failed to load chats"));
    }, search ? 300 : 0);
    return () => clearTimeout(timer);
  }, [open, search, session?.user?.email]);

  const addable = (conversations ?? []).filter((c) => !c.task_status);

  const add = async (conversation: Conversation) => {
    setBusyId(conversation.conversation_id);
    setError(null);
    try {
      await updateTask(conversation.conversation_id, { task_status: "active" });
      onOpenChange(false);
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add chat");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add existing chat</DialogTitle>
        </DialogHeader>
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search your chats..."
          autoFocus
        />
        <div className="max-h-80 space-y-0.5 overflow-y-auto">
          {conversations === null ? (
            [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-9 w-full" />)
          ) : addable.length === 0 ? (
            <p className="px-1 py-3 text-center text-xs text-muted-foreground">
              {conversations.length > 0
                ? "All matching chats are already on the board"
                : "No chats found"}
            </p>
          ) : (
            addable.map((c) => (
              <button
                key={c.conversation_id}
                onClick={() => add(c)}
                disabled={busyId !== null}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left",
                  "hover:bg-muted disabled:opacity-50",
                  busyId === c.conversation_id && "opacity-50",
                )}
              >
                <span className={cn(
                  "h-2 w-2 shrink-0 rounded-full",
                  statusDotStyles[c.status] ?? "bg-muted-foreground/40",
                )} />
                <span className="min-w-0 flex-1 truncate text-[13px]">
                  {c.title || c.prompt || "Untitled"}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  <ClientTimestamp iso={c.started_at} variant="short" placeholder="" />
                </span>
              </button>
            ))
          )}
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
      </DialogContent>
    </Dialog>
  );
}
