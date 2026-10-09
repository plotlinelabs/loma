"use client";

import { useRef, useState } from "react";
import { RiSendPlaneLine, RiLoader4Line, RiAttachmentLine, RiUploadLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { createTask, type ChatFile, type Task } from "@/lib/api";
import { filesToChatFiles, filesFromClipboard } from "@/lib/chatFiles";
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
}

/** Bottom-pinned capture box on the tasks board (mobile and desktop) — the
 * same composer controls as chat (model picker, attachments). Typing here
 * fires the task immediately: the agent starts running in the background
 * (survives closing the app) and the backend titles the task from the prompt
 * with an LLM. */
export function QuickAddTask({ onAdded, boardId, onOpenTask }: QuickAddTaskProps) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
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
  const voice = useVoiceDispatcher({ boardId, model: selectedModel || undefined, toolConfig, onBoardChanged: onAdded, onOpenTask });

  const addFiles = async (fileList: FileList | File[]) => {
    const { files: chatFiles, rejected } = await filesToChatFiles(fileList);
    if (chatFiles.length) setFiles((prev) => [...prev, ...chatFiles]);
    if (rejected.length) console.warn("Unsupported files skipped:", rejected);
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
  const textarea = (
    <Textarea
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          void submit();
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
        isMobile ? "min-w-0 flex-1 self-center px-1 py-2.5 overflow-y-auto dark:bg-transparent" : "w-full px-3 pt-3 pb-1.5 overflow-hidden",
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
    <div
      className="relative shrink-0 border-t border-border bg-background px-3 pt-2 pb-2 max-md:px-0 max-md:pt-1.5 max-md:pb-0"
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
      <div className="mx-auto w-full max-w-3xl">
      {error && <p className="mb-1 text-xs text-destructive">{error}</p>}
      {!voice.active && voice.error && <p className="mb-1 text-xs text-destructive">{voice.error}</p>}
      <PendingFilesStrip files={files} onRemove={(i) => setFiles((prev) => prev.filter((_, idx) => idx !== i))} />
      {voice.active && isMobile && <VoicePanel voice={voice} className="mb-1.5 rounded-2xl border border-border bg-card" />}
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
      ) : (
      <div data-slot="task-composer" className="flex flex-col bg-card border border-border rounded-xl focus-within:border-input transition-colors">
        {voice.active && <VoicePanel voice={voice} className="border-b border-border" />}
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
    </div>
  );
}
