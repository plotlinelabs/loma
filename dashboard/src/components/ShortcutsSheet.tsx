"use client";

import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useIsMac } from "./tasks/boardBits";

type Shortcut = { keys: string[]; label: string };

function groups(mac: boolean): { title: string; items: Shortcut[] }[] {
  const mod = mac ? "⌘" : "Ctrl";
  const alt = mac ? "⌥" : "Alt";
  return [
    {
      title: "Anywhere",
      items: [
        { keys: [mod, "K"], label: "Search chats, tasks and boards" },
        { keys: [alt, "1–9"], label: "Jump to a board" },
        { keys: [mod, "/"], label: "Show these shortcuts" },
      ],
    },
    {
      title: "In a chat",
      items: [
        { keys: ["Enter"], label: "Send" },
        { keys: ["Shift", "Enter"], label: "New line" },
        { keys: ["↑"], label: "Edit your last message (empty composer)" },
        { keys: ["Shift", "Tab"], label: "Plan first on or off" },
        { keys: ["/"], label: "Commands" },
        { keys: ["Esc"], label: "Stop Loma" },
      ],
    },
    {
      title: "My tasks, as a list",
      items: [
        { keys: ["J", "K"], label: "Next and previous task" },
        { keys: ["E"], label: "Mark done and open the next" },
        { keys: ["R"], label: "Reply" },
      ],
    },
  ];
}

/** Every keyboard shortcut on one sheet: Cmd/Ctrl+/ anywhere, or ? outside text fields. */
export default function ShortcutsSheet() {
  const [open, setOpen] = useState(false);
  const mac = useIsMac();

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey;
      const typing = (event.target as HTMLElement | null)?.closest("input, textarea, select, [contenteditable='true']");
      if ((mod && event.key === "/") || (event.key === "?" && !mod && !typing)) {
        event.preventDefault();
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Dictation’s shortcut is set from Keyboard shortcuts in your account menu.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-5">
          {groups(mac).map((group) => (
            <section key={group.title}>
              <h3 className="mb-2 text-[13px] font-semibold text-muted-foreground">{group.title}</h3>
              <dl className="flex flex-col gap-2">
                {group.items.map((item) => (
                  <div key={item.label} className="flex items-center justify-between gap-4 text-[14px]">
                    <dt>{item.label}</dt>
                    <dd className="flex shrink-0 gap-1">
                      {item.keys.map((key) => (
                        <kbd key={key} className="min-w-6 rounded-md border border-border bg-muted px-1.5 py-0.5 text-center font-sans text-[12px]">
                          {key}
                        </kbd>
                      ))}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
