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

interface QuickAddTaskProps {
  /** Lane new tasks land in (the board's first staging lane). */
  laneId: string;
  onAdded: () => void;
}

/** Bottom-pinned capture box on the mobile tasks board — the same composer
 * controls as chat (model picker, attachments): type what you need done and
 * it becomes a staged task; the backend titles it from the prompt with an
 * LLM, and the model/attachments ride along when the task starts. */
export function QuickAddTask({ laneId, onAdded }: QuickAddTaskProps) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [busy, setBusy] = useState(false);
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
    try {
      await createTask({
        prompt: prompt || files.map((f) => f.name).join(", "),
        lane: laneId,
        model: selectedModel || undefined,
        files: files.length > 0 ? files : undefined,
      });
      setValue("");
      setFiles([]);
      onAdded();
    } catch {
      // Keep the text so nothing is lost; the user can retry.
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
