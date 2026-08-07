"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  RiArrowLeftLine,
  RiCheckboxCircleFill,
  RiMicLine,
  RiSendPlaneFill,
  RiStopFill,
  RiVolumeMuteLine,
  RiVolumeUpLine,
} from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ModelPicker } from "@/components/composer/ModelPicker";
import { useAgentModels } from "@/hooks/useAgentModels";
import { cn } from "@/lib/utils";
import { useDictation } from "@/hooks/useDictation";
import {
  sendVoiceCommand,
  type VoiceAction,
  type VoiceHistoryMessage,
} from "@/lib/api";

/** Human label for an executed board action — shown as a chip under the
 * assistant's reply so the user can glance-confirm what changed. */
const ACTION_LABELS: Record<string, string> = {
  create_task: "Task created",
  start_task: "Task started",
  add_input: "Input sent to task",
  mark_done: "Marked done",
  park_task: "Task parked",
  set_priority: "Priority updated",
  set_deadline: "Deadline updated",
};

interface VoiceTurn extends VoiceHistoryMessage {
  action?: VoiceAction;
  executed?: boolean;
}

/** Voice Mode — a hands-free, connected session over the tasks board.
 *
 * Push-to-talk: the mic button records (existing dictation pipeline →
 * /api/transcribe), the transcript goes to /api/voice/command, and the short
 * reply is spoken with the browser's speechSynthesis. A text composer is the
 * no-mic fallback (desktop, permissions denied, tests). */
export default function VoicePage() {
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  const [thinking, setThinking] = useState(false);
  const [speechOn, setSpeechOn] = useState(true);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { models, selectedModel, selectModel, loadState } = useAgentModels(undefined, false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const turnsRef = useRef<VoiceTurn[]>([]);
  turnsRef.current = turns;
  const speechOnRef = useRef(speechOn);
  speechOnRef.current = speechOn;

  const speak = useCallback((text: string) => {
    if (!speechOnRef.current || typeof speechSynthesis === "undefined") return;
    try {
      speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.05;
      speechSynthesis.speak(utterance);
    } catch {
      // TTS is best-effort — the reply is on screen either way.
    }
  }, []);

  // Leaving the page stops any in-flight speech.
  useEffect(
    () => () => {
      if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
    },
    [],
  );

  const handleUtterance = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setError(null);
      // History = what the model saw before this utterance.
      const history = turnsRef.current.map(({ role, content }) => ({ role, content }));
      setTurns((prev) => [...prev, { role: "user", content: trimmed }]);
      setThinking(true);
      try {
        const res = await sendVoiceCommand(trimmed, history, selectedModel);
        setTurns((prev) => [
          ...prev,
          {
            role: "assistant",
            content: res.speech,
            action: res.action,
            executed: res.executed,
          },
        ]);
        speak(res.speech);
      } catch (e) {
        const message = e instanceof Error ? e.message : "Something went wrong";
        setError(message);
      } finally {
        setThinking(false);
      }
    },
    [selectedModel, speak],
  );

  const dictation = useDictation((text) => void handleUtterance(text));

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, thinking]);

  const micState =
    dictation.state === "recording"
      ? "Listening… tap to stop"
      : dictation.state === "transcribing"
        ? "Transcribing…"
        : thinking
          ? "Thinking…"
          : "Tap to speak";

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <Link
          href="/tasks"
          className="text-muted-foreground hover:text-foreground press-scale"
          aria-label="Back to tasks"
        >
          <RiArrowLeftLine size={20} />
        </Link>
        <h1 className="font-heading text-base font-semibold">Voice Mode</h1>
        <span className="text-xs text-muted-foreground">tasks, hands-free</span>
        <Button
          variant="ghost"
          size="icon"
          className="ml-auto"
          onClick={() => {
            if (speechOn && typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
            setSpeechOn((on) => !on);
          }}
          aria-label={speechOn ? "Mute spoken replies" : "Unmute spoken replies"}
        >
          {speechOn ? <RiVolumeUpLine size={18} /> : <RiVolumeMuteLine size={18} />}
        </Button>
      </div>

      {/* Transcript */}
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {turns.length === 0 && !thinking && (
          <div className="mx-auto mt-10 max-w-sm text-center text-sm text-muted-foreground">
            <p className="font-medium text-foreground">Talk to your task board.</p>
            <p className="mt-2">
              Try “what needs my attention?”, “what finished today?”, “create a
              task to review the July invoices”, or “tell the website task to
              also check mobile”.
            </p>
          </div>
        )}
        {turns.map((turn, i) => (
          <div
            key={i}
            className={cn("flex", turn.role === "user" ? "justify-end" : "justify-start")}
          >
            <div
              className={cn(
                "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm",
                turn.role === "user"
                  ? "rounded-br-md bg-primary text-primary-foreground"
                  : "rounded-bl-md bg-muted text-foreground",
              )}
            >
              <p className="whitespace-pre-wrap">{turn.content}</p>
              {turn.executed && turn.action && ACTION_LABELS[turn.action.type] && (
                <span className="mt-1.5 flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                  <RiCheckboxCircleFill size={14} />
                  {ACTION_LABELS[turn.action.type]}
                </span>
              )}
            </div>
          </div>
        ))}
        {thinking && (
          <div className="flex justify-start">
            <div className="rounded-2xl rounded-bl-md bg-muted px-3.5 py-2 text-sm text-muted-foreground">
              <span className="inline-flex gap-1">
                <span className="animate-bounce">·</span>
                <span className="animate-bounce [animation-delay:120ms]">·</span>
                <span className="animate-bounce [animation-delay:240ms]">·</span>
              </span>
            </div>
          </div>
        )}
        {(error || dictation.error) && (
          <p className="text-center text-xs text-destructive">{error || dictation.error}</p>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Mic + text fallback */}
      <div className="shrink-0 border-t border-border px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3">
        <div className="mb-3 flex justify-center">
          <ModelPicker
            models={models}
            selectedModel={selectedModel}
            onSelect={selectModel}
            loadState={loadState}
            disabled={thinking || dictation.state === "recording"}
          />
        </div>
        <div className="flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={dictation.toggle}
            disabled={!selectedModel || !dictation.supported || dictation.state === "transcribing" || thinking}
            aria-label={dictation.state === "recording" ? "Stop recording" : "Start recording"}
            className={cn(
              "flex h-16 w-16 items-center justify-center rounded-full text-white shadow-lg transition-colors press-scale",
              dictation.state === "recording"
                ? "animate-pulse bg-red-500"
                : "bg-primary disabled:opacity-40",
            )}
          >
            {dictation.state === "recording" ? <RiStopFill size={26} /> : <RiMicLine size={26} />}
          </button>
          <span className="text-xs text-muted-foreground">
            {dictation.supported ? (
              dictation.state === "recording" ? `${micState} · ${dictation.seconds}s` : micState
            ) : (
              "No microphone available — type instead"
            )}
          </span>
        </div>
        <form
          className="mt-3 flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const text = typed;
            setTyped("");
            void handleUtterance(text);
          }}
        >
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="Or type a command…"
            className="h-9 text-sm"
            disabled={thinking}
          />
          <Button
            type="submit"
            size="icon"
            variant="ghost"
            className="shrink-0"
            disabled={!selectedModel || !typed.trim() || thinking}
            aria-label="Send"
          >
            <RiSendPlaneFill size={18} />
          </Button>
        </form>
      </div>
    </div>
  );
}
