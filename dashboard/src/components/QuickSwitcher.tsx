"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { RiAddLine, RiChat1Line, RiChatHistoryLine, RiCheckboxLine, RiNotification3Line, RiTimeLine } from "@remixicon/react";
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
import { fetchNeedsYouTasks, searchChats, type ChatSearchResult, type NeedsYouTask } from "@/lib/api";
import { relativeAge } from "@/lib/server-time";
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

  // Chats: recent ones while the box is empty, else every chat whose title or
  // messages match. Results are matched on the server, so cmdk must not drop them.
  const [query, setQuery] = useState("");
  const [chats, setChats] = useState<{ query: string; results: ChatSearchResult[] }>({ query: "", results: [] });
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchChats(q, q ? 8 : 5, controller.signal)
        .then((results) => setChats({ query: q, results }))
        .catch(() => {});
    }, q ? 200 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [open, query]);

  const boardById = new Map(boards.map((b) => [b.id, b]));
  // Tasks on a board you can no longer open are left out.
  const waiting = tasks.filter((task) => boardById.has(task.board_id));
  const run = (action: () => void) => () => { setOpen(false); setQuery(""); action(); };
  const openChat = (chat: ChatSearchResult) => {
    if (chat.task_board_id && boardById.has(chat.task_board_id)) openBoard(chat.task_board_id, chat.conversation_id);
    else router.push(`/chat?continue=${chat.conversation_id}`);
  };
  const shownChats = chats.query === query.trim() ? chats.results : [];

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Search chats, tasks and boards"
      description="Search every chat and task you've had with Loma, and jump to a board."
      className="sm:max-w-xl"
    >
      <Command>
        <CommandInput placeholder="Search chats, tasks and boards…" value={query} onValueChange={setQuery} />
        <CommandList className="max-h-[26rem]">
          <CommandEmpty>No chats, tasks or boards found.</CommandEmpty>
          {shownChats.length > 0 && (
            <CommandGroup heading={query.trim() ? "Chats and tasks" : "Recent chats"}>
              {shownChats.map((chat) => (
                <CommandItem
                  key={chat.conversation_id}
                  value={`chat ${chat.title ?? ""} ${query} ${chat.conversation_id}`}
                  onSelect={run(() => openChat(chat))}
                  className="items-start"
                >
                  {chat.task_status ? <RiCheckboxLine className="mx-0.5 mt-0.5" /> : <RiChatHistoryLine className="mx-0.5 mt-0.5" />}
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="flex items-baseline gap-2">
                      <span className="min-w-0 truncate font-medium">{chat.title || "Untitled chat"}</span>
                      {chat.task_board_id && boardById.get(chat.task_board_id) && (
                        <span className="shrink-0 truncate text-[12px] text-muted-foreground">
                          {boardById.get(chat.task_board_id)?.emoji} {boardById.get(chat.task_board_id)?.name}
                        </span>
                      )}
                    </span>
                    {chat.snippet && (
                      <span className="line-clamp-2 text-[13px] leading-snug text-muted-foreground">
                        {chat.snippet_role === "user" && "You: "}
                        <Highlighted text={chat.snippet} query={query.trim()} />
                      </span>
                    )}
                  </span>
                  <CommandShortcut className="shrink-0 tracking-normal">{relativeAge(chat.last_active)}</CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
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

/** text with each case-insensitive occurrence of query marked. */
function Highlighted({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  const parts: ReactNode[] = [];
  let from = 0;
  for (let at = lower.indexOf(needle); at >= 0; at = lower.indexOf(needle, from)) {
    if (at > from) parts.push(text.slice(from, at));
    parts.push(<mark key={at} className="rounded-sm bg-brand-500/20 px-0.5 text-foreground">{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
  }
  parts.push(text.slice(from));
  return <>{parts}</>;
}
