"use client";

import { RiLoader4Line, RiMicLine, RiStopFill } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { useDictation } from "@/hooks/useDictation";
import { cn } from "@/lib/utils";

/** Append a transcript to existing composer text with sane spacing. */
export function appendDictation(prev: string, text: string): string {
  if (!prev) return text;
  return prev + (/\s$/.test(prev) ? "" : " ") + text;
}

interface DictationButtonProps {
  onText: (text: string) => void;
  disabled?: boolean;
  className?: string;
}

/** Mic toggle for composers: tap to record, tap again to stop + transcribe
 * (server-side, gpt-4o-transcribe). Hidden where MediaRecorder/getUserMedia
 * are unavailable (e.g. plain HTTP). */
export function DictationButton({ onText, disabled, className }: DictationButtonProps) {
  const { state, seconds, error, supported, toggle } = useDictation(onText);

  if (!supported) return null;

  if (state === "recording") {
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={toggle}
        title="Stop and transcribe"
        className={cn(
          "h-7 gap-1.5 rounded-lg px-2 text-red-600 hover:text-red-600 hover:bg-red-500/10",
          className,
        )}
      >
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-red-500" />
        </span>
        <span className="text-xs tabular-nums">
          {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}
        </span>
        <RiStopFill size={16} />
      </Button>
    );
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={toggle}
      disabled={disabled || state === "transcribing"}
      title={error ?? "Dictate"}
      className={cn(
        error ? "text-destructive" : "text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      {state === "transcribing"
        ? <RiLoader4Line size={16} className="animate-spin" />
        : <RiMicLine size={16} />}
    </Button>
  );
}
