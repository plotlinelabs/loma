"use client";

import { useEffect, useState } from "react";
import {
  RiAddLine,
  RiArrowDownSLine,
  RiArrowUpSLine,
  RiDeleteBinLine,
} from "@remixicon/react";
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
import { fetchBoardSettings, saveBoardSettings } from "@/lib/api";

interface EditableLane {
  id?: string;
  name: string;
}

interface BoardSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Count of staged tasks per lane id — used for delete warnings. */
  laneCounts: Record<string, number>;
  onSaved: () => void;
}

export function BoardSettingsDialog({ open, onOpenChange, laneCounts, onSaved }: BoardSettingsDialogProps) {
  const [prompt, setPrompt] = useState("");
  const [lanes, setLanes] = useState<EditableLane[]>([]);
  const [removedWithTasks, setRemovedWithTasks] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setRemovedWithTasks([]);
    fetchBoardSettings()
      .then((settings) => {
        setPrompt(settings.prompt);
        setLanes(settings.lanes.map(({ id, name }) => ({ id, name })));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load settings"));
  }, [open]);

  const moveLane = (index: number, delta: number) => {
    const next = [...lanes];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setLanes(next);
  };

  const removeLane = (index: number) => {
    const lane = lanes[index];
    const count = lane.id ? laneCounts[lane.id] ?? 0 : 0;
    if (count > 0) {
      const firstRemaining = lanes.find((_, i) => i !== index);
      setRemovedWithTasks((prev) => [
        ...prev,
        `${count} task${count === 1 ? "" : "s"} in “${lane.name}” will move to “${firstRemaining?.name}”`,
      ]);
    }
    setLanes(lanes.filter((_, i) => i !== index));
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveBoardSettings({ prompt, lanes });
      onOpenChange(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Board settings</DialogTitle>
        </DialogHeader>
        <div className="space-y-5">
          <div className="space-y-1.5">
            <Label htmlFor="board-prompt">Context</Label>
            <Textarea
              id="board-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Your role and working context — added to every task on this board"
              rows={5}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Columns</Label>
            <div className="space-y-1">
              {lanes.map((lane, index) => (
                <div key={lane.id ?? `new-${index}`} className="flex items-center gap-1">
                  <Input
                    value={lane.name}
                    onChange={(e) =>
                      setLanes(lanes.map((l, i) => (i === index ? { ...l, name: e.target.value } : l)))
                    }
                    className="h-8"
                  />
                  <Button
                    variant="ghost" size="icon" className="h-8 w-8 shrink-0"
                    disabled={index === 0}
                    onClick={() => moveLane(index, -1)}
                    title="Move up"
                  >
                    <RiArrowUpSLine className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost" size="icon" className="h-8 w-8 shrink-0"
                    disabled={index === lanes.length - 1}
                    onClick={() => moveLane(index, 1)}
                    title="Move down"
                  >
                    <RiArrowDownSLine className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost" size="icon" className="h-8 w-8 shrink-0"
                    disabled={lanes.length <= 1}
                    onClick={() => removeLane(index)}
                    title="Delete column"
                  >
                    <RiDeleteBinLine className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
            <Button
              variant="ghost" size="sm" className="text-muted-foreground"
              onClick={() => setLanes([...lanes, { name: "" }])}
            >
              <RiAddLine className="h-4 w-4" />
              Add column
            </Button>
            {removedWithTasks.map((warning) => (
              <p key={warning} className="text-xs text-amber-600">{warning}</p>
            ))}
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
