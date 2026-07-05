"use client";

import { useState } from "react";
import { RiSendPlaneLine, RiLoader4Line } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { createTask } from "@/lib/api";

interface QuickAddTaskProps {
  /** Lane new tasks land in (the board's first staging lane). */
  laneId: string;
  onAdded: () => void;
}

/** Bottom-pinned capture box on the mobile tasks board: type what you need
 * done and it becomes a staged task immediately — the backend titles it from
 * the prompt with an LLM. */
export function QuickAddTask({ laneId, onAdded }: QuickAddTaskProps) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const prompt = value.trim();
    if (!prompt || busy) return;
    setBusy(true);
    try {
      await createTask({ prompt, lane: laneId });
      setValue("");
      onAdded();
    } catch {
      // Keep the text so nothing is lost; the user can retry.
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shrink-0 border-t border-border bg-background px-3 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))]">
      <div className="flex items-end gap-2 rounded-2xl border border-border bg-muted px-3 py-1.5">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          placeholder="What do you need done?"
          rows={1}
          className="min-h-0 flex-1 resize-none overflow-hidden border-0 bg-transparent p-0 py-1.5 focus-visible:ring-0 focus-visible:border-transparent rounded-none"
          style={{ maxHeight: "120px" }}
        />
        <Button
          size="icon-sm"
          onClick={() => void submit()}
          disabled={!value.trim() || busy}
          aria-label="Add task"
          className="mb-0.5 shrink-0 rounded-lg"
        >
          {busy
            ? <RiLoader4Line size={16} className="animate-spin" />
            : <RiSendPlaneLine size={16} />}
        </Button>
      </div>
    </div>
  );
}
