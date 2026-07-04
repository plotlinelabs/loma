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
import { fetchAgentModels, type AgentModel, type BoardLane, type Task } from "@/lib/api";

interface TaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lanes: BoardLane[];
  /** When set, the dialog edits an existing draft instead of creating one. */
  task?: Task | null;
  defaultLane?: string;
  onSubmit: (
    values: { title: string; prompt: string; lane: string; model: string },
    start: boolean,
  ) => Promise<void>;
}

export function TaskDialog({ open, onOpenChange, lanes, task, defaultLane, onSubmit }: TaskDialogProps) {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [lane, setLane] = useState(lanes[0]?.id ?? "todo");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<AgentModel[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTitle(task?.title ?? "");
      setPrompt(task?.prompt ?? "");
      setLane(task?.task_lane ?? defaultLane ?? lanes[0]?.id ?? "todo");
      setModel(task?.model ?? "");
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, task]);

  // Load the model catalog once per dialog open; default new tasks to the
  // backend's default model so the selection is always explicit.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchAgentModels()
      .then((catalog) => {
        if (cancelled) return;
        setModels(catalog.models || []);
        setModel((current) =>
          current && (catalog.models || []).some((m) => m.id === current)
            ? current
            : catalog.default_model || catalog.models?.[0]?.id || "",
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [open]);

  const submit = async (start: boolean) => {
    if (!prompt.trim()) {
      setError("Details are required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ title: title.trim(), prompt: prompt.trim(), lane, model }, start);
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
          <DialogTitle>{task ? "Task details" : "New task"}</DialogTitle>
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
            <Label htmlFor="task-details">Details</Label>
            <Textarea
              id="task-details"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="What should the agent do?"
              rows={6}
            />
          </div>
          <div className="flex gap-3">
            {lanes.length > 1 && (
              <div className="flex-1 space-y-1.5">
                <Label>Lane</Label>
                <Select value={lane} onValueChange={setLane}>
                  <SelectTrigger className="w-full">
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
            {models.length > 0 && (
              <div className="flex-1 space-y-1.5">
                <Label>Model</Label>
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Default" />
                  </SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
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
