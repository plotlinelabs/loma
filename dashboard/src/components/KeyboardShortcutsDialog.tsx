"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useIsMac } from "@/components/tasks/boardBits";
import { useDictationShortcut } from "@/hooks/useDictationShortcut";
import {
  DEFAULT_DICTATION_SHORTCUT,
  formatShortcut,
  isModifierCode,
  serializeShortcut,
  shortcutFromEvent,
  validateShortcut,
} from "@/lib/dictation-shortcut";
import { cn } from "@/lib/utils";

/** Settings > Keyboard shortcuts: lets each user pick their dictation combo. */
export function KeyboardShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const mac = useIsMac();
  const [shortcut, setShortcut] = useDictationShortcut();
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!recording) return;
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.code === "Escape" && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
        setRecording(false);
        setError("");
        return;
      }
      if (isModifierCode(event.code)) return; // wait for the main key
      const next = shortcutFromEvent(event);
      const problem = validateShortcut(next);
      if (problem) { setError(problem); return; }
      setShortcut(next);
      setError("");
      setRecording(false);
    };
    // Capture phase so the dialog's own Escape/typing handlers don't see it.
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [recording, setShortcut]);

  const isDefault = serializeShortcut(shortcut) === serializeShortcut(DEFAULT_DICTATION_SHORTCUT);

  return (
    <Dialog open={open} onOpenChange={(next) => {
      if (recording) return;
      setError("");
      onOpenChange(next);
    }}>
      <DialogContent data-keyboard-shortcuts="true" className="sm:max-w-md">
        <DialogTitle>Keyboard shortcuts</DialogTitle>
        <DialogDescription>Saved in this browser.</DialogDescription>

        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-medium text-foreground">Dictation</div>
              <div className="text-xs text-muted-foreground">Start and stop voice typing while the cursor is in a message box.</div>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              data-testid="dictation-shortcut-record"
              onClick={() => { setError(""); setRecording((r) => !r); }}
              aria-label={recording ? "Recording shortcut, press keys or Escape to cancel" : `Change dictation shortcut, currently ${formatShortcut(shortcut, mac)}`}
              className={cn("min-w-[140px] shrink-0 font-mono text-xs", recording && "ring-2 ring-accent-200 text-muted-foreground")}
            >
              {recording ? "Press keys…" : formatShortcut(shortcut, mac)}
            </Button>
          </div>

          {recording && !error && (
            <p className="text-xs text-muted-foreground">Press the new combo, e.g. {mac ? "Option" : "Alt"} + D. Esc cancels.</p>
          )}
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}

          <div className="flex justify-end gap-2">
            {shortcut && (
              <Button type="button" variant="ghost" size="sm" onClick={() => { setRecording(false); setError(""); setShortcut(null); }}>
                Turn off
              </Button>
            )}
            {!isDefault && (
              <Button type="button" variant="ghost" size="sm" onClick={() => { setRecording(false); setError(""); setShortcut(DEFAULT_DICTATION_SHORTCUT); }}>
                Reset to {formatShortcut(DEFAULT_DICTATION_SHORTCUT, mac)}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
