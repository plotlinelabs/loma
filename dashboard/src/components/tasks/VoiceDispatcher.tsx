"use client";

import { useEffect, useRef } from "react";
import {
  RiCheckLine, RiCloseLine, RiErrorWarningLine, RiExternalLinkLine, RiLoader4Line,
  RiMicLine, RiMicOffLine, RiVoiceprintLine,
} from "@remixicon/react";
import PetCompanion from "../PetCompanion";
import { Button } from "@/components/ui/button";
import { basePath } from "@/lib/api";
import type { useVoiceDispatcher, VoiceState } from "@/hooks/useVoiceDispatcher";
import { cn } from "@/lib/utils";

type Voice = ReturnType<typeof useVoiceDispatcher>;

const STATE_LABEL: Record<VoiceState, string> = {
  idle: "",
  connecting: "Connecting",
  listening: "Listening",
  working: "Working",
  speaking: "Speaking",
};

const PET_STATE = { idle: "idle", connecting: "working", listening: "listening", working: "working", speaking: "idle" } as const;

/** Starts voice mode from the task composer. Sits beside the dictation mic:
 * the mic fills the text box, this opens a live conversation. */
export function VoiceModeButton({ voice, className }: { voice: Voice; className?: string }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={() => void voice.start()}
      title={voice.error ?? "Talk to Loma"}
      aria-label="Start voice mode"
      data-testid="voice-start"
      className={cn(
        voice.error ? "text-destructive" : "text-muted-foreground hover:text-foreground",
        "max-md:size-11 max-md:rounded-full max-md:[&_svg]:size-5",
        className,
      )}
    >
      <RiVoiceprintLine size={16} />
    </Button>
  );
}

/** Live voice session panel: state, captions, and each action voice took
 * with a link to the task it touched, or chips for links from the open task. */
export function VoicePanel({ voice, className }: { voice: Voice; className?: string }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const { state, lines, actions, muted, error } = voice;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lines, actions]);

  const label = muted && state === "listening" ? "Muted" : STATE_LABEL[state];
  return (
    <div data-slot="voice-panel" role="region" aria-label="Voice mode" data-testid="voice-panel" className={cn("flex flex-col gap-1.5 px-3 py-2", className)}>
      <div className="flex items-center gap-2">
        <PetCompanion size={24} state={PET_STATE[state]} />
        <span className="text-[13px] font-medium text-foreground" aria-live="polite">{label}</span>
        {state === "connecting"
          ? <RiLoader4Line size={14} className="animate-spin text-muted-foreground" />
          : (
            <span aria-hidden="true" data-state={muted ? "muted" : state} className="voice-wave flex h-4 items-center gap-0.5">
              {[0, 1, 2, 3, 4].map((i) => <span key={i} style={{ animationDelay: `${i * 0.12}s` }} />)}
            </span>
          )}
        <div className="ml-auto flex items-center gap-1">
          <Button
            type="button" variant="ghost" size="sm" onClick={voice.toggleMute}
            data-testid="voice-mute"
            disabled={state === "connecting"}
            aria-pressed={muted}
            className={cn("h-7 gap-1.5 rounded-lg px-2 text-xs max-md:h-10", muted ? "text-red-600 hover:text-red-600" : "text-muted-foreground hover:text-foreground")}
          >
            {muted ? <RiMicOffLine size={14} /> : <RiMicLine size={14} />}
            {muted ? "Unmute" : "Mute"}
          </Button>
          <Button
            type="button" variant="ghost" size="sm" onClick={voice.stop} data-testid="voice-end"
            className="h-7 gap-1.5 rounded-lg px-2 text-xs text-muted-foreground hover:text-foreground max-md:h-10"
          >
            <RiCloseLine size={14} />
            End
          </Button>
        </div>
      </div>
      {(lines.length > 0 || actions.length > 0) && (
        <div ref={scrollRef} className="flex max-h-32 flex-col gap-1 overflow-y-auto text-[13px] leading-snug">
          {lines.slice(-6).map((line, i) => (
            <p key={`${line.speaker}-${i}`} className={line.speaker === "user" ? "text-muted-foreground" : "text-foreground"}>
              <span className="font-medium">{line.speaker === "user" ? "You: " : "Loma: "}</span>
              {line.text}
            </p>
          ))}
          {actions.slice(-4).map((action) => (
            <div key={action.id}>
              <p className={cn("flex items-center gap-1.5", action.ok ? "text-foreground" : "text-destructive")}>
                {action.ok ? <RiCheckLine size={14} className="shrink-0 text-green-600" /> : <RiErrorWarningLine size={14} className="shrink-0" />}
                <span className="min-w-0 truncate">{action.label}</span>
                {action.conversationId && (
                  <a
                    href={`${basePath}/chat?continue=${action.conversationId}`}
                    target="_blank" rel="noopener noreferrer"
                    className="inline-flex shrink-0 items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
                  >
                    Open task
                    <RiExternalLinkLine size={12} />
                  </a>
                )}
              </p>
              {action.links?.length ? (
                <div className="mt-1 flex flex-wrap gap-1.5 pl-5" data-testid="voice-link-chips">
                  {action.links.map((link, _, all) => (
                    <a
                      key={link.href}
                      href={link.href}
                      target="_blank" rel="noopener noreferrer"
                      title={link.href}
                      data-testid="voice-link-chip"
                      className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-foreground hover:bg-accent max-md:py-1.5"
                    >
                      {all.length > 1 && <span className="font-medium text-muted-foreground">{link.number}</span>}
                      <span className="min-w-0 truncate">{link.label}</span>
                      <RiExternalLinkLine size={12} className="shrink-0 text-muted-foreground" />
                    </a>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <p className="text-[10px] text-muted-foreground">AI-generated voice · Audio processed by OpenAI</p>
      <style>{`
        .voice-wave span { width: 2px; height: 4px; border-radius: 1px; background: currentColor; opacity: .55; }
        .voice-wave[data-state="listening"] span { animation: voice-wave 1.1s ease-in-out infinite; }
        .voice-wave[data-state="speaking"] span { animation: voice-wave .55s ease-in-out infinite; opacity: .9; }
        .voice-wave[data-state="working"] span { animation: voice-wave 1.6s ease-in-out infinite; opacity: .35; }
        @keyframes voice-wave { 0%, 100% { height: 4px; } 50% { height: 14px; } }
        @media (prefers-reduced-motion: reduce) { .voice-wave span { animation: none !important; } }
      `}</style>
    </div>
  );
}
