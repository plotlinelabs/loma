"use client";

import { useRef, useState } from "react";
import { RiSendPlaneLine, RiLoader4Line, RiAttachmentLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { createTask, type ChatFile } from "@/lib/api";
import { filesToChatFiles } from "@/lib/chatFiles";
import { useAgentModels } from "@/hooks/useAgentModels";
import { ModelPicker } from "@/components/composer/ModelPicker";
import { PendingFilesStrip } from "@/components/composer/PendingFilesStrip";
import { DictationButton, appendDictation } from "@/components/composer/DictationButton";

interface QuickAddTaskProps {
  onAdded: () => void;
}

/** Bottom-pinned capture box on the mobile tasks board — the same composer
 * controls as chat (model picker, attachments). Typing here fires the task
 * immediately: the agent starts running in the background (survives closing
 * the app) and the backend titles the task from the prompt with an LLM. */
export function QuickAddTask({ onAdded }: QuickAddTaskProps) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { models, selectedModel, selectModel, loadState } = useAgentModels();

  const addFiles = async (fileList: FileList | File[]) => {
    const { files: chatFiles, rejected } = await filesToChatFiles(fileList);
    if (chatFiles.length) setFiles((prev) => [...prev, ...chatFiles]);
    if (rejected.length) console.warn("Unsupported files skipped:", rejected);
  };

  const submit = async () => {
    const prompt = value.trim();
    if ((!prompt && files.length === 0) || busy) return;
    setBusy(true);
    setError(null);
    try {
      await createTask({
        prompt: prompt || files.map((f) => f.name).join(", "),
        model: selectedModel || undefined,
        files: files.length > 0 ? files : undefined,
        start: true,
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

  return (
    <div className="shrink-0 border-t border-border bg-background px-3 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))]">
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
      {error && <p className="mb-1 text-xs text-destructive">{error}</p>}
      <PendingFilesStrip files={files} onRemove={(i) => setFiles((prev) => prev.filter((_, idx) => idx !== i))} />
      <div className="flex flex-col bg-muted border border-border rounded-2xl focus-within:border-gray-300 transition-colors">
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
            const pasted = Array.from(e.clipboardData?.files ?? []);
            if (pasted.length) {
              e.preventDefault();
              void addFiles(pasted);
            }
          }}
          placeholder="What do you need done?"
          rows={1}
          className="w-full bg-transparent px-3 pt-3 pb-1.5 text-[13px] text-foreground placeholder-muted-foreground focus:outline-none resize-none overflow-hidden border-0 focus-visible:ring-0 focus-visible:border-transparent rounded-none min-h-0"
          style={{ maxHeight: "120px" }}
        />
        <div className="flex items-center justify-between gap-2 px-2 pb-2">
          <div className="flex min-w-0 items-center">
            <ModelPicker
              models={models}
              selectedModel={selectedModel}
              onSelect={selectModel}
              loadState={loadState}
            />
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <DictationButton onText={(t) => setValue((prev) => appendDictation(prev, t))} />
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
            <Button
              type="button"
              size="icon-sm"
              onClick={() => void submit()}
              disabled={(!value.trim() && files.length === 0) || busy}
              aria-label="Add task"
              className="bg-accent-200 hover:bg-accent-300 disabled:opacity-30 disabled:hover:bg-accent-200 text-accent-on rounded-lg press-scale"
            >
              {busy
                ? <RiLoader4Line size={16} className="animate-spin" />
                : <RiSendPlaneLine size={16} />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
