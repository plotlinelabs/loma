"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { BoardLane, Task } from "@/lib/api";

interface TaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lanes: BoardLane[];
  /** When set, the dialog edits an existing draft instead of creating one. */
  task?: Task | null;
  defaultLane?: string;
  onSubmit: (values: { title: string; prompt: string; lane: string }, start: boolean) => Promise<void>;
}

export function TaskDialog({ open, onOpenChange, lanes, task, defaultLane, onSubmit }: TaskDialogProps) {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [lane, setLane] = useState(lanes[0]?.id ?? "todo");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTitle(task?.title ?? "");
      setPrompt(task?.prompt ?? "");
      setLane(task?.task_lane ?? defaultLane ?? lanes[0]?.id ?? "todo");
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, task]);

  const submit = async (start: boolean) => {
    if (!prompt.trim()) {
      setError("A prompt is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ title: title.trim(), prompt: prompt.trim(), lane }, start);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{task ? "Edit task" : "New task"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="task-title">Title</Label>
            <Input
              id="task-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="task-prompt">Prompt</Label>
            <Textarea
              id="task-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="What should the agent do?"
              rows={6}
            />
          </div>
          {lanes.length > 1 && (
            <div className="space-y-1.5">
              <Label>Lane</Label>
              <Select value={lane} onValueChange={setLane}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {lanes.map((l) => (
                    <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="outline" onClick={() => submit(false)} disabled={busy}>
            {task ? "Save" : "Add"}
          </Button>
          <Button onClick={() => submit(true)} disabled={busy}>
            {task ? "Save & start" : "Add & start"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
