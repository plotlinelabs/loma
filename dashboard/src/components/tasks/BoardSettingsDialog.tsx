"use client";

import { useEffect, useState } from "react";
import {
  RiAddLine,
  RiArrowDownSLine,
  RiArrowRightSLine,
  RiArrowUpSLine,
  RiDeleteBinLine,
} from "@remixicon/react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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
  const [defaultContext, setDefaultContext] = useState("");
  const [showDefault, setShowDefault] = useState(false);
  const [lanes, setLanes] = useState<EditableLane[]>([]);
  const [removedWithTasks, setRemovedWithTasks] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setRemovedWithTasks([]);
    setShowDefault(false);
    fetchBoardSettings()
      .then((settings) => {
        setPrompt(settings.prompt);
        setDefaultContext(settings.default_context ?? "");
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
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl"
      >
        <SheetHeader className="flex-shrink-0 border-b border-border pr-12">
          <SheetTitle>Board settings</SheetTitle>
          <SheetDescription>Context and columns for your tasks board.</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4">
          {defaultContext && (
            <div className="space-y-1.5">
              <button
                type="button"
                onClick={() => setShowDefault((v) => !v)}
                className="flex items-center gap-1 text-[13px] font-medium text-foreground"
                aria-expanded={showDefault}
              >
                {showDefault
                  ? <RiArrowDownSLine className="h-4 w-4 text-muted-foreground" />
                  : <RiArrowRightSLine className="h-4 w-4 text-muted-foreground" />}
                Team default context
              </button>
              <p className="text-xs text-muted-foreground">
                Set by admins in Admin › Settings. Applied to every task before your personal context below.
              </p>
              {showDefault && (
                <pre className="max-h-80 overflow-y-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/40 px-3 py-2 font-mono text-xs leading-5 text-muted-foreground">
                  {defaultContext}
                </pre>
              )}
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="board-prompt">{defaultContext ? "Personal context" : "Context"}</Label>
            <p className="text-xs text-muted-foreground">
              Your role and working context — added to every task on this board.
            </p>
            <Textarea
              id="board-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g. I lead the CS team. Prefer short answers. Always reply in my Slack voice."
              rows={12}
              className="min-h-[240px] resize-y font-mono text-xs leading-5 md:text-xs"
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
        <SheetFooter className="mt-0 flex-shrink-0 flex-row justify-end border-t border-border">
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>Save</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
