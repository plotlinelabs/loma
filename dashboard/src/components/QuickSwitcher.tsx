"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { RiAddLine, RiChat1Line, RiNotification3Line, RiTimeLine } from "@remixicon/react";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { fetchNeedsYouTasks, type NeedsYouTask } from "@/lib/api";
import { useBoards } from "@/lib/BoardsContext";
import { useUser } from "@/lib/UserContext";
import { cn } from "@/lib/utils";
import { BoardEmoji, NeedsYouCount, boardShortcut, useIsMac } from "./tasks/boardBits";

/** Quick switcher (Cmd/Ctrl+K) and the board jump keys (Alt/Option+1-9),
 * available on every page. */
export default function QuickSwitcher() {
  const router = useRouter();
  const { hasRole } = useUser();
  const { boards, currentBoardId, openBoard, createBoard, commandOpen: open, setCommandOpen: setOpen } = useBoards();
  const mac = useIsMac();

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
        return;
      }
      // Boards 1-9: Alt/Option+number. Cmd/Ctrl+number also works where the
      // browser passes it on (the installed app); in a tab it switches tabs.
      const digit = /^Digit([1-9])$/.exec(event.code);
      if (!digit || event.shiftKey || mod === event.altKey) return;
      const target = event.target as HTMLElement | null;
      // Option+number types a character on a Mac, so leave text fields alone.
      if (event.altKey && target?.closest("input, textarea, [contenteditable='true']")) return;
      const next = boards[Number(digit[1]) - 1];
      if (!next) return;
      event.preventDefault();
      setOpen(false);
      openBoard(next.id);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [boards, openBoard, setOpen]);

  const [tasks, setTasks] = useState<NeedsYouTask[]>([]);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchNeedsYouTasks().then((list) => { if (!cancelled) setTasks(list); }).catch(() => {});
    return () => { cancelled = true; };
  }, [open]);

  const boardById = new Map(boards.map((b) => [b.id, b]));
  // Tasks on a board you can no longer open are left out.
  const waiting = tasks.filter((task) => boardById.has(task.board_id));
  const run = (action: () => void) => () => { setOpen(false); action(); };

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Jump to a board or task"
      description="Search your boards and the tasks waiting on you."
      className="sm:max-w-lg"
    >
      <Command>
        <CommandInput placeholder="Jump to a board or task…" />
        <CommandList className="max-h-[26rem]">
          <CommandEmpty>No boards or tasks found.</CommandEmpty>
          <CommandGroup heading="Boards">
            {boards.map((board, index) => (
              <CommandItem key={board.id} value={`board ${board.name} ${board.id}`} onSelect={run(() => openBoard(board.id))}>
                <BoardEmoji emoji={board.emoji} />
                <span className={cn("min-w-0 truncate", board.id === currentBoardId && "font-medium")}>{board.name}</span>
                <NeedsYouCount count={board.needs_you ?? 0} />
                <CommandShortcut className="tracking-normal">{boardShortcut(index, mac)}</CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
          {waiting.length > 0 && (
            <CommandGroup heading="Waiting on you">
              {waiting.map((task) => (
                <CommandItem
                  key={task.conversation_id}
                  value={`task ${task.title ?? ""} ${boardById.get(task.board_id)?.name} ${task.conversation_id}`}
                  onSelect={run(() => openBoard(task.board_id, task.conversation_id))}
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center">
                    <span className={cn("h-2 w-2 rounded-full", task.status === "completed" ? "bg-amber-500" : "bg-destructive")} />
                  </span>
                  <span className="min-w-0 truncate">{task.title || "Untitled task"}</span>
                  <CommandShortcut className="max-w-[40%] shrink-0 truncate tracking-normal">
                    {boardById.get(task.board_id)?.emoji} {boardById.get(task.board_id)?.name}
                  </CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          <CommandGroup heading="Actions">
            <CommandItem value="action new chat start conversation" onSelect={run(() => router.push("/chat"))}>
              <RiChat1Line className="mx-0.5" /> New chat
            </CommandItem>
            <CommandItem value="action new board create" onSelect={run(createBoard)}>
              <RiAddLine className="mx-0.5" /> New board
            </CommandItem>
            {hasRole("analyst") && (
              <CommandItem value="action flows schedules automations" onSelect={run(() => router.push("/flows"))}>
                <RiTimeLine className="mx-0.5" /> Flows
              </CommandItem>
            )}
            <CommandItem value="action notifications inbox bell" onSelect={run(() => router.push("/notifications"))}>
              <RiNotification3Line className="mx-0.5" /> Notifications
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
