"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { RiSendPlaneLine, RiLoader4Line, RiAttachmentLine, RiUploadLine, RiEditLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { createTask, type ChatFile, type Task } from "@/lib/api";
import { filesToChatFiles, filesFromClipboard, skippedFilesNotice } from "@/lib/chatFiles";
import { useAgentModels } from "@/hooks/useAgentModels";
import { useToolsPicker } from "@/hooks/useToolsPicker";
import { ModelPicker } from "@/components/composer/ModelPicker";
import { ToolsPicker } from "@/components/composer/ToolsPicker";
import { ComposerSettings } from "@/components/composer/ComposerSettings";
import { PendingFilesStrip } from "@/components/composer/PendingFilesStrip";
import { DictationButton, appendDictation } from "@/components/composer/DictationButton";
import { useFileDrop } from "@/components/composer/useFileDrop";
import { useIsMobile } from "@/hooks/useIsMobile";
import { useVoiceAvailable, useVoiceDispatcher } from "@/hooks/useVoiceDispatcher";
import { VoiceModeButton, VoicePanel } from "./VoiceDispatcher";
import { cn } from "@/lib/utils";

interface QuickAddTaskProps {
  onAdded: () => void;
  /** Board the task is added to; omitted = the caller's own board. */
  boardId?: string;
  /** Lets voice mode show a task without leaving the board (desktop chat drawer). */
  onOpenTask?: (task: Task) => void;
  /** Lets voice mode close that drawer again; returns the task it closed, if any. */
  onCloseTask?: () => Task | null;
  /** True while a task drawer is open over the board. Voice mode then moves
   * from the inline composer panel to a floating pill above the drawer. */
  drawerOpen?: boolean;
  /** Voice touched a specific task's run/messages — lets the open drawer for
   * that task refresh in place. */
  onTaskActivity?: (conversationId: string) => void;
  /** Desktop board: render as a slim floating pill over the board instead of a
   * full-width bar. The toolbar shows only while the composer is in use. */
  floating?: boolean;
  /** Floating only: the board is scrolling, so slide the pill away unless it is in use. */
  scrollHidden?: boolean;
}

/** Bottom-pinned capture box on the tasks board (mobile and desktop) — the
 * same composer controls as chat (model picker, attachments). Typing here
 * fires the task immediately: the agent starts running in the background
 * (survives closing the app) and the backend titles the task from the prompt
 * with an LLM. */
export function QuickAddTask({ onAdded, boardId, onOpenTask, onCloseTask, drawerOpen, onTaskActivity, floating = false, scrollHidden = false }: QuickAddTaskProps) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Floating pill: toolbar (model, tools, attach) only while the user is engaged.
  const [expanded, setExpanded] = useState(false);
  const isMobile = useIsMobile();
  const { models, selectedModel, selectModel, loadState } = useAgentModels();
  const {
    tools: availableTools,
    skills: availableSkills,
    selection: toolsSelection,
    loadState: toolsLoadState,
    loadCatalog: loadToolsCatalog,
    setEnabled,
    setAll,
    toolConfig,
  } = useToolsPicker();
  const voiceAvailable = useVoiceAvailable();
  const voice = useVoiceDispatcher({ boardId, model: selectedModel || undefined, toolConfig, onBoardChanged: onAdded, onOpenTask, onCloseTask, onTaskActivity });
  // Portal target is only there on the client; gate the floating pill on mount.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  // A drawer covers the composer, so voice mode floats above it as a pill.
  const floatingVoice = voice.active && !!drawerOpen;

  const addFiles = async (fileList: FileList | File[]) => {
    const { files: chatFiles, rejected } = await filesToChatFiles(fileList);
    if (chatFiles.length) setFiles((prev) => [...prev, ...chatFiles]);
    setError(skippedFilesNotice(rejected));
  };

  const { isDragOver, dropHandlers } = useFileDrop((f) => void addFiles(f));

  const submit = async () => {
    const prompt = value.trim();
    if ((!prompt && files.length === 0) || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (voice.active) {
        if (files.length) throw new Error("End voice mode to submit a task with attachments.");
        voice.sendText(prompt);
        setValue("");
        return;
      }
      await createTask({
        prompt: prompt || files.map((f) => f.name).join(", "),
        model: selectedModel || undefined,
        files: files.length > 0 ? files : undefined,
        start: true,
        tool_config: toolConfig,
        board: boardId,
      });
      setValue("");
      setFiles([]);
      onAdded();
    } catch (e) {
      // Keep the text so nothing is lost; the user can retry.
      setError(e instanceof Error ? e.message : "Failed to add task");
    } finally {
      setBusy(false);
    }
  };

  const empty = !value.trim() && files.length === 0;
  // Never collapse or hide the pill while something is in flight or typed.
  const engaged = expanded || !!value || files.length > 0 || busy || voice.active || !!error;
  const pillHidden = floating && scrollHidden && !engaged;

  // Collapse on an outside click, but not when the click lands in a picker
  // or menu the toolbar opened (they portal to <body>).
  useEffect(() => {
    if (!floating || !expanded) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Element | null;
      if (!target || rootRef.current?.contains(target)) return;
      if (target.closest("[data-radix-popper-content-wrapper],[role='dialog'],[role='menu'],[role='listbox']")) return;
      setExpanded(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [floating, expanded]);

  // "/" focuses the floating composer from anywhere on the board.
  useEffect(() => {
    if (!floating) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      if (document.querySelector("[role='dialog']")) return;
      e.preventDefault();
      setExpanded(true);
      textareaRef.current?.focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [floating]);

  const focusPill = () => {
    setExpanded(true);
    textareaRef.current?.focus();
  };
  const textarea = (
    <Textarea
      ref={textareaRef}
      data-testid="quick-add-task-input"
      value={value}
      onFocus={floating ? () => setExpanded(true) : undefined}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          void submit();
        } else if (floating && e.key === "Escape" && empty) {
          setExpanded(false);
          e.currentTarget.blur();
        }
      }}
      onPaste={(e) => {
        const pasted = filesFromClipboard(e.clipboardData);
        if (pasted.length) {
          e.preventDefault();
          void addFiles(pasted);
        }
      }}
      placeholder={voice.active ? "Type to Loma..." : "What do you need done?"}
      rows={1}
      className={cn(
        "bg-transparent text-[13px] text-foreground placeholder-muted-foreground focus:outline-none resize-none border-0 focus-visible:ring-0 focus-visible:border-transparent rounded-none min-h-0",
        // The one-row phone field grows with its text (field-sizing), up to maxHeight.
        isMobile ? "min-w-0 flex-1 self-center px-1 py-2.5 overflow-y-auto dark:bg-transparent"
          : floating ? cn("min-w-0 flex-1 px-3 overflow-hidden", engaged ? "pt-3 pb-1.5" : "self-center py-2")
          : "w-full px-3 pt-3 pb-1.5 overflow-hidden",
      )}
      style={{ maxHeight: "120px" }}
    />
  );
  const toolsPicker = (
    <ToolsPicker tools={availableTools} skills={availableSkills} selection={toolsSelection} onSetEnabled={setEnabled} onSetAll={setAll} onOpen={loadToolsCatalog} loadState={toolsLoadState} />
  );
  const modelPicker = (
    <ModelPicker
      models={models}
      selectedModel={selectedModel}
      onSelect={selectModel}
      loadState={loadState}
    />
  );
  // Voice mode replaces both mics while it is on; Mute and End live in its panel.
  const micButtons = voice.active ? null : (
    <>
      <DictationButton
        onText={(t) => setValue((prev) => appendDictation(prev, t))}
        mobileProminent
        hideIdleOnMobile={!empty}
        compactMobile
      />
      {voiceAvailable && <VoiceModeButton voice={voice} className={cn(!empty && "max-md:hidden")} />}
    </>
  );
  const sendButton = (
    <Button
      type="button"
      size="icon-sm"
      onClick={() => void submit()}
      disabled={empty || busy}
      aria-label="Add task"
      className={cn(
        "bg-primary text-primary-foreground hover:bg-accent-200 hover:text-accent-on disabled:opacity-40 disabled:hover:bg-primary disabled:hover:text-primary-foreground rounded-lg press-scale max-md:size-11 max-md:rounded-full",
        empty && "max-md:hidden",
      )}
    >
      {busy
        ? <RiLoader4Line size={16} className="animate-spin" />
        : <RiSendPlaneLine size={16} />}
    </Button>
  );

  return (
    <>
    <div
      ref={rootRef}
      data-testid={floating ? "quick-add-task-floating" : undefined}
      data-state={floating ? (pillHidden ? "hidden" : engaged ? "expanded" : "resting") : undefined}
      className={cn(
        floating
          // Floats over the board's bottom edge; the side gutters stay board space.
          ? "pointer-events-none absolute inset-x-0 bottom-3 z-20 px-4 transition-[transform,opacity] duration-200 ease-out motion-reduce:transition-none"
          : "relative shrink-0 border-t border-border bg-background px-3 pt-2 pb-2 max-md:px-0 max-md:pt-1.5 max-md:pb-0",
        pillHidden && "translate-y-[calc(100%+1rem)] opacity-0",
      )}
      {...dropHandlers}
    >
      {isDragOver && (
        <div className="absolute inset-0 z-50 flex items-center justify-center rounded-xl border-2 border-dashed border-brand-400 bg-brand-50/80">
          <div className="flex items-center gap-2 text-[13px] font-medium text-brand-600">
            <RiUploadLine size={20} />
            Drop files here
          </div>
        </div>
      )}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) {
            void addFiles(e.target.files);
            e.target.value = "";
          }
        }}
      />
      {/* Centered + width-capped so the composer stays readable on the wide
          desktop board; on a phone max-w-3xl is simply full width. */}
      <div className={cn("mx-auto w-full", floating ? cn("pointer-events-auto transition-[max-width] duration-200 motion-reduce:transition-none", engaged ? "max-w-2xl" : "max-w-[560px]") : "max-w-3xl")}
        inert={pillHidden || undefined}>
      {error && <p className="mb-1 text-xs text-destructive">{error}</p>}
      {!voice.active && voice.error && <p className="mb-1 text-xs text-destructive">{voice.error}</p>}
      <PendingFilesStrip files={files} onRemove={(i) => setFiles((prev) => prev.filter((_, idx) => idx !== i))} />
      {voice.active && isMobile && !floatingVoice && <VoicePanel voice={voice} className="mb-1.5 rounded-2xl border border-border bg-card" />}
      {isMobile ? (
        /* Phones: one row. Attach, model, tools and skills sit behind "+",
           so the capture box costs the board one line instead of two. */
        <div data-slot="task-composer" className="flex items-end gap-0.5 rounded-[26px] border border-border bg-card p-1 focus-within:border-input transition-colors">
          <ComposerSettings trigger="plus" title="Task options" description="Attach files, or choose the model, tools and skills for this task." onAttach={() => fileInputRef.current?.click()}>
            {modelPicker}
            {toolsPicker}
          </ComposerSettings>
          {textarea}
          {micButtons}
          {sendButton}
        </div>
      ) : floating ? (
        /* One tree for resting and expanded so the textarea and mic never remount
           (focus, draft and an active dictation survive the toggle). */
        <div data-slot="task-composer" onClick={engaged ? undefined : focusPill}
          className={cn("flex flex-col border border-border bg-card shadow-lg ring-1 ring-black/5 focus-within:border-input transition-colors", engaged ? "rounded-2xl" : "cursor-text rounded-[28px]")}>
          {voice.active && !floatingVoice && <VoicePanel voice={voice} className="border-b border-border" />}
          <div className={cn("flex gap-1 pr-2", engaged ? "items-end pb-1" : "h-14 items-center pl-2")}>
            {textarea}
            {!engaged && <kbd className="hidden shrink-0 rounded border border-border px-1.5 text-[11px] text-muted-foreground lg:inline">/</kbd>}
            <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
              {micButtons}
              {sendButton}
            </div>
          </div>
          {engaged && (
            <div className="flex items-center gap-1 px-2 pb-2">
              <div className="min-w-0">{modelPicker}</div>
              {toolsPicker}
              <Button type="button" variant="ghost" size="icon-sm" onClick={() => fileInputRef.current?.click()}
                title="Attach files" className="ml-auto text-muted-foreground hover:text-foreground">
                <RiAttachmentLine size={16} />
              </Button>
            </div>
          )}
        </div>
      ) : (
      <div data-slot="task-composer" className="flex flex-col bg-card border border-border rounded-xl focus-within:border-input transition-colors">
        {voice.active && !floatingVoice && <VoicePanel voice={voice} className="border-b border-border" />}
        {textarea}
        <div className="flex items-center justify-between gap-2 px-2 pb-2">
          <div className="flex min-w-0 items-center gap-1">
            <div className="min-w-0">{modelPicker}</div>
            {toolsPicker}
          </div>
          <div className="ml-auto flex items-center gap-1 shrink-0">
            {micButtons}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => fileInputRef.current?.click()}
              title="Attach files"
              className="text-muted-foreground hover:text-foreground"
            >
              <RiAttachmentLine size={16} />
            </Button>
            {sendButton}
          </div>
        </div>
      </div>
      )}
      </div>
      {/* Drawer open: voice mode lifts out of the composer into a floating pill
          that sits above the drawer (Sheet is z-50), so the live session,
          captions, Mute/End and link chips stay reachable while a task is on screen.
          The modal drawer disables pointer events outside it, hence pointer-events-auto. */}
      {mounted && floatingVoice && createPortal(
        <div
          data-slot="voice-pill"
          className="pointer-events-auto fixed bottom-4 left-1/2 z-[60] w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2"
        >
          <VoicePanel voice={voice} className="rounded-2xl border border-border bg-popover shadow-lg ring-1 ring-black/5" />
        </div>,
        document.body,
      )}
    </div>
      {floating && pillHidden && (
        <Button
          type="button"
          size="icon"
          data-testid="quick-add-task-reveal"
          aria-label="New task"
          onClick={focusPill}
          className="absolute bottom-4 right-4 z-20 size-11 rounded-full bg-primary text-primary-foreground shadow-lg press-scale animate-in fade-in zoom-in-90"
        >
          <RiEditLine size={18} />
        </Button>
      )}
    </>
  );
}
