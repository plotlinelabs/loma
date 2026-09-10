"use client";

import { useId, useRef, useState } from "react";
import { RiCloseLine, RiLoader4Line, RiAttachmentLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { createTask, type BoardLane, type ChatFile } from "@/lib/api";
import { filesToChatFiles } from "@/lib/chatFiles";
import { useAgentModels } from "@/hooks/useAgentModels";
import { useToolsPicker } from "@/hooks/useToolsPicker";
import { useIsMobile } from "@/hooks/useIsMobile";
import { ModelPicker } from "@/components/composer/ModelPicker";
import { ToolsPicker } from "@/components/composer/ToolsPicker";
import { PendingFilesStrip } from "@/components/composer/PendingFilesStrip";
import { DictationButton, appendDictation } from "@/components/composer/DictationButton";

interface QuickAddTaskProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lanes: BoardLane[];
  lane: string;
  onLaneChange: (lane: string) => void;
  anchor: HTMLElement | null;
  onAdded: (started: boolean) => void;
}

/** One on-demand capture flow for list buttons and the board header.
 * Keep this component mounted: dismissing preserves text and attachments,
 * but never creates a conversation until the user explicitly submits. */
export function QuickAddTask({ open, onOpenChange, lanes, lane, onLaneChange, anchor, onAdded }: QuickAddTaskProps) {
  const isMobile = useIsMobile();
  const id = useId();
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const swipeStartRef = useRef<{ x: number; y: number } | null>(null);
  const submittingRef = useRef(false);
  const uploadsRef = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { models, selectedModel, selectModel, loadState } = useAgentModels();
  const {
    tools: availableTools, skills: availableSkills, selection: toolsSelection,
    loadState: toolsLoadState, loadCatalog: loadToolsCatalog, toggleTool, toggleSkill,
    enableAll: enableAllTools, isAllEnabled: allToolsEnabled,
    disabledCount: toolsDisabledCount, toolConfig, isAlwaysEnabled,
  } = useToolsPicker();
  const selectedLane = lanes.some((item) => item.id === lane) ? lane : lanes[0]?.id ?? "";
  const disabled = busy || uploading || !selectedLane || (!value.trim() && !files.length);

  const changeOpen = (next: boolean) => {
    if (!submittingRef.current) onOpenChange(next);
  };
  const addFiles = async (fileList: FileList | File[]) => {
    if (submittingRef.current) return;
    uploadsRef.current += 1;
    setUploading(true);
    try {
      const { files: chatFiles, rejected } = await filesToChatFiles(fileList);
      if (chatFiles.length) setFiles((prev) => [...prev, ...chatFiles]);
      if (rejected.length) setError("Some files could not be attached. Please check the attachments before submitting.");
    } catch {
      setError("Could not read the attachment. Please try again.");
    } finally {
      uploadsRef.current -= 1;
      setUploading(uploadsRef.current > 0);
    }
  };

  const submit = async (start: boolean) => {
    if (submittingRef.current || uploadsRef.current || disabled) return;
    submittingRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await createTask({
        prompt: value.trim() || files.map((file) => file.name).join(", "),
        lane: selectedLane,
        model: selectedModel || undefined,
        files: files.length ? files : undefined,
        start,
        tool_config: toolConfig,
      });
      setValue("");
      setFiles([]);
      onOpenChange(false);
      onAdded(start);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add task");
    } finally {
      submittingRef.current = false;
      setBusy(false);
    }
  };

  const content = (
    <div className="flex min-h-0 flex-col gap-3">
      <div
        className="pr-8"
        onTouchStart={(event) => {
          const touch = event.touches[0];
          if (isMobile && touch) swipeStartRef.current = { x: touch.clientX, y: touch.clientY };
        }}
        onTouchCancel={() => { swipeStartRef.current = null; }}
        onTouchEnd={(event) => {
          const touch = event.changedTouches[0];
          const start = swipeStartRef.current;
          swipeStartRef.current = null;
          if (isMobile && touch && start && touch.clientY - start.y > 60 && Math.abs(touch.clientX - start.x) < 40) changeOpen(false);
        }}
      >
        {isMobile ? <DialogTitle>New task</DialogTitle> : <h2 id={`${id}-title`} className="font-medium">New task</h2>}
        {isMobile
          ? <DialogDescription className="mt-1">Save an idea or start working with Loma.</DialogDescription>
          : <p className="mt-1 text-xs text-muted-foreground">Save an idea or start working with Loma.</p>}
      </div>
      <Button type="button" variant="ghost" size="icon-sm" className="absolute right-2 top-2" aria-label="Close task composer" disabled={busy} onClick={() => changeOpen(false)}>
        <RiCloseLine size={18} />
      </Button>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      <fieldset disabled={busy} className="flex min-h-0 flex-col gap-3 disabled:opacity-70">
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => {
          if (e.target.files?.length) { void addFiles(e.target.files); e.target.value = ""; }
        }} />
        <Textarea
          ref={inputRef}
          aria-label="Task details"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void submit(e.shiftKey);
            }
          }}
          onPaste={(e) => {
            const pasted = Array.from(e.clipboardData?.files ?? []);
            if (pasted.length) { e.preventDefault(); void addFiles(pasted); }
          }}
          placeholder="What do you need done?"
          rows={4}
          className="max-h-48 min-h-24 resize-none overflow-y-auto max-md:text-base"
        />
        <PendingFilesStrip files={files} onRemove={(i) => setFiles((prev) => prev.filter((_, idx) => idx !== i))} />
        <div className="flex items-center gap-2">
          <label htmlFor={`${id}-lane`} className="text-xs text-muted-foreground">List</label>
          <select id={`${id}-lane`} value={selectedLane} onChange={(e) => onLaneChange(e.target.value)} className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm">
            {lanes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <DictationButton disabled={busy} onText={(text) => setValue((prev) => appendDictation(prev, text))} />
          <Button type="button" variant="ghost" size="icon-sm" onClick={() => fileInputRef.current?.click()} aria-label="Attach files">
            <RiAttachmentLine size={16} />
          </Button>
        </div>
        <details className="text-xs text-muted-foreground">
          <summary className="w-fit cursor-pointer py-1">Model & tools</summary>
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <ModelPicker models={models} selectedModel={selectedModel} onSelect={selectModel} loadState={loadState} />
            <ToolsPicker tools={availableTools} skills={availableSkills} selection={toolsSelection} onToggleTool={toggleTool} onToggleSkill={toggleSkill} onEnableAll={enableAllTools} onOpen={loadToolsCatalog} isAllEnabled={allToolsEnabled} disabledCount={toolsDisabledCount} loadState={toolsLoadState} isAlwaysEnabled={isAlwaysEnabled} />
          </div>
        </details>
      </fieldset>
      <div className="sticky bottom-0 flex gap-2 border-t border-border bg-card pt-3">
        <Button variant="outline" className="flex-1 max-md:h-11" disabled={disabled} onClick={() => void submit(false)}>Add task</Button>
        <Button className="flex-1 max-md:h-11" disabled={disabled} onClick={() => void submit(true)}>
          {busy && <RiLoader4Line size={16} className="animate-spin" />} Add & start
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{uploading ? "Reading attachments..." : "Add task saves without running Loma."}</p>
    </div>
  );

  if (isMobile) return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent showCloseButton={false} className="max-md:top-auto max-md:bottom-[calc(100dvh-var(--app-h,100dvh))] max-md:max-w-full max-md:rounded-b-none max-md:pb-[max(1rem,env(safe-area-inset-bottom))]" onOpenAutoFocus={(e) => { e.preventDefault(); inputRef.current?.focus(); }}>
        {content}
      </DialogContent>
    </Dialog>
  );
  return (
    <Popover open={open} onOpenChange={changeOpen} modal>
      <PopoverAnchor virtualRef={{ current: { getBoundingClientRect: () => anchor?.getBoundingClientRect() ?? new DOMRect(100, 100, 0, 0) } }} />
      <PopoverContent align="start" side="bottom" collisionPadding={16} aria-labelledby={`${id}-title`} className="relative w-[420px] max-w-[calc(100vw-2rem)] max-h-[var(--radix-popover-content-available-height)] overflow-y-auto bg-card p-4" onOpenAutoFocus={(e) => { e.preventDefault(); inputRef.current?.focus(); }} onCloseAutoFocus={(e) => { e.preventDefault(); anchor?.focus(); }}>
        {content}
      </PopoverContent>
    </Popover>
  );
}
