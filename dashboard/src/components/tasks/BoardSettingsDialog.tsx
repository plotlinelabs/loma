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
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { fetchBoardSettings, saveBoardSettings, type BoardFieldType } from "@/lib/api";
import { FIELD_TYPE_LABELS } from "./cardDisplay";

interface EditableLane {
  id?: string;
  name: string;
}

interface EditableField {
  id?: string;
  name: string;
  type: BoardFieldType;
  /** Comma-separated while editing (select types only). */
  options: string;
  show_on_card: boolean;
}

const hasOptions = (type: BoardFieldType) => type === "select" || type === "multi_select";

interface BoardSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Count of staged tasks per lane id — used for delete warnings. */
  laneCounts: Record<string, number>;
  onSaved: () => void;
  /** Board being edited; omitted = the caller's own board. */
  boardId?: string;
  /** Shown in the header so it's clear which board is being changed. */
  boardName?: string;
  /** Shared board: lanes, tags and context apply to everyone on it. */
  shared?: boolean;
  /** Card board: columns hold cards, which carry the custom fields edited here. */
  cardMode?: boolean;
}

export function BoardSettingsDialog({ open, onOpenChange, laneCounts, onSaved, boardId, boardName, shared = false, cardMode = false }: BoardSettingsDialogProps) {
  const [fields, setFields] = useState<EditableField[]>([]);
  const [showAgentWork, setShowAgentWork] = useState(true);
  const [loading, setLoading] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [defaultContext, setDefaultContext] = useState("");
  const [showDefault, setShowDefault] = useState(false);
  const [lanes, setLanes] = useState<EditableLane[]>([]);
  const [removedWithTasks, setRemovedWithTasks] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setRemovedWithTasks([]);
    setShowDefault(false);
    fetchBoardSettings(boardId)
      .then((settings) => {
        setShowAgentWork(settings.show_agent_work !== false);
        setLoading(false);
        setPrompt(settings.prompt);
        setDefaultContext(settings.default_context ?? "");
        setLanes(settings.lanes.map(({ id, name }) => ({ id, name })));
        setFields((settings.fields ?? []).map((field) => ({ ...field, options: field.options.join(", ") })));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load settings"));
  }, [open, boardId]);

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
        `${count} ${cardMode ? "card" : "task"}${count === 1 ? "" : "s"} in “${lane.name}” will move to “${firstRemaining?.name}”`,
      ]);
    }
    setLanes(lanes.filter((_, i) => i !== index));
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveBoardSettings({
        prompt, lanes, show_agent_work: showAgentWork,
        ...(cardMode ? {
          fields: fields.map((field) => ({
            ...field,
            options: hasOptions(field.type) ? field.options.split(",").map((o) => o.trim()).filter(Boolean) : [],
          })),
        } : {}),
      }, boardId);
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
          <SheetTitle>{boardName ? `${boardName} settings` : "Board settings"}</SheetTitle>
          <SheetDescription>
            {shared
              ? "Context and columns for this shared board. Changes apply to everyone on it."
              : "Context and columns for your tasks board."}
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4">
          {!cardMode && <div className="flex items-center justify-between gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="show-agent-work">Show Agent work card</Label>
              <p className="text-xs text-muted-foreground">Show live agent work above your tasks. Hiding it does not stop any work.</p>
            </div>
            <Switch id="show-agent-work" checked={showAgentWork} onCheckedChange={setShowAgentWork} disabled={loading || busy} />
          </div>}
          {cardMode && (
            <div className="space-y-1.5">
              <Label>Fields</Label>
              <p className="text-xs text-muted-foreground">
                The details every card on this board can hold. Loma sees them on each task in a card.
              </p>
              <div className="space-y-1.5">
                {fields.map((field, index) => {
                  const update = (changes: Partial<EditableField>) =>
                    setFields(fields.map((f, i) => (i === index ? { ...f, ...changes } : f)));
                  return (
                    <div key={field.id ?? `new-${index}`} className="space-y-1.5 rounded-lg border border-border p-2" data-field-row>
                      <div className="flex items-center gap-1">
                        <Input
                          value={field.name}
                          onChange={(e) => update({ name: e.target.value })}
                          placeholder="Field name"
                          maxLength={40}
                          aria-label="Field name"
                          className="h-8"
                        />
                        {/* The type is fixed once saved: cards already hold values of that type. */}
                        <Select value={field.type} onValueChange={(type) => update({ type: type as BoardFieldType })} disabled={!!field.id}>
                          <SelectTrigger className="w-36 shrink-0" aria-label="Field type"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {Object.entries(FIELD_TYPE_LABELS).map(([type, label]) => (
                              <SelectItem key={type} value={type}>{label}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Button
                          variant="ghost" size="icon" className="h-8 w-8 shrink-0"
                          onClick={() => setFields(fields.filter((_, i) => i !== index))}
                          title="Delete field"
                        >
                          <RiDeleteBinLine className="h-4 w-4" />
                        </Button>
                      </div>
                      {hasOptions(field.type) && (
                        <Input
                          value={field.options}
                          onChange={(e) => update({ options: e.target.value })}
                          placeholder="Options, separated by commas"
                          aria-label={`Options for ${field.name || "field"}`}
                          className="h-8"
                        />
                      )}
                      <label className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Switch checked={field.show_on_card} onCheckedChange={(checked) => update({ show_on_card: checked })} />
                        Show on card
                      </label>
                    </div>
                  );
                })}
              </div>
              <Button
                variant="ghost" size="sm" className="text-muted-foreground"
                disabled={fields.length >= 20}
                onClick={() => setFields([...fields, { name: "", type: "text", options: "", show_on_card: true }])}
              >
                <RiAddLine className="h-4 w-4" />
                Add field
              </Button>
              {fields.some((field) => field.id) && (
                <p className="text-xs text-muted-foreground">Deleting a field also removes its values from every card.</p>
              )}
            </div>
          )}
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
            <Label htmlFor="board-prompt">{shared ? "Board context" : defaultContext ? "Personal context" : "Context"}</Label>
            <p className="text-xs text-muted-foreground">
              {shared
                ? "Shared working context — added to every task on this board."
                : "Your role and working context — added to every task on this board."}
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
          <Button onClick={save} disabled={busy || loading}>Save</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
