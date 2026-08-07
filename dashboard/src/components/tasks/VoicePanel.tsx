"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  RiCloseLine,
  RiCheckboxCircleFill,
  RiRadioButtonLine,
  RiRestartLine,
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
import { useLiveDictation } from "@/hooks/useLiveDictation";
import {
  fetchTasksBoard,
  generateVoiceSpeech,
  sendVoiceCommand,
  type Task,
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
  actions?: VoiceAction[];
  executed?: boolean;
}

const VOICE_HISTORY_KEY = "loma.voice.history";
const VOICE_CHOICE_KEY = "loma.voice.choice";
const VOICE_SPEED_KEY = "loma.voice.speed";
const VOICES = [
  { id: "rachel", label: "Rachel · warm" },
  { id: "adam", label: "Adam · confident" },
  { id: "bella", label: "Bella · expressive" },
  { id: "antoni", label: "Antoni · calm" },
];

/** Voice Mode — a hands-free, connected session over the tasks board.
 *
 * Push-to-talk: the mic button records (existing dictation pipeline →
 * /api/transcribe), the transcript goes to /api/voice/command, and the short
 * Deepgram live endpointing), and the short reply is spoken with ElevenLabs.
 * A text composer is the
 * no-mic fallback (desktop, permissions denied, tests). */
export function VoicePanel({ onClose, onBoardChange }: { onClose: () => void; onBoardChange?: () => void }) {
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  const [thinking, setThinking] = useState(false);
  const [speechOn, setSpeechOn] = useState(true);
  const [connected, setConnected] = useState(true);
  const [speaking, setSpeaking] = useState(false);
  const [typed, setTyped] = useState("");
  const [voice, setVoice] = useState("rachel");
  const [voiceSpeed, setVoiceSpeed] = useState(1.1);
  const [error, setError] = useState<string | null>(null);
  const { models, selectedModel, selectModel, loadState } = useAgentModels(undefined, false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const turnsRef = useRef<VoiceTurn[]>([]);
  turnsRef.current = turns;
  const speechOnRef = useRef(speechOn);
  speechOnRef.current = speechOn;
  const connectedRef = useRef(connected);
  connectedRef.current = connected;
  const thinkingRef = useRef(thinking);
  thinkingRef.current = thinking;
  const taskStatesRef = useRef<Map<string, string> | null>(null);
  const dictationStartRef = useRef<() => void>(() => {});
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const submittingRef = useRef(false);
  const speechQueueRef = useRef<string[]>([]);
  const speechGenerationRef = useRef(0);
  const pumpingSpeechRef = useRef(false);
  const sessionGenerationRef = useRef(0);

  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(VOICE_HISTORY_KEY) || "[]");
      if (Array.isArray(saved)) setTurns(saved.slice(-24));
    } catch {
      sessionStorage.removeItem(VOICE_HISTORY_KEY);
    }
    const savedVoice = localStorage.getItem(VOICE_CHOICE_KEY);
    if (savedVoice && VOICES.some(({ id }) => id === savedVoice)) setVoice(savedVoice);
    const savedSpeed = Number(localStorage.getItem(VOICE_SPEED_KEY));
    if (savedSpeed >= 0.7 && savedSpeed <= 1.2) setVoiceSpeed(savedSpeed);
  }, []);

  useEffect(() => {
    if (turns.length) sessionStorage.setItem(VOICE_HISTORY_KEY, JSON.stringify(turns.slice(-24)));
  }, [turns]);

  const stopSpeech = useCallback(() => {
    speechGenerationRef.current += 1;
    speechQueueRef.current = [];
    pumpingSpeechRef.current = false;
    audioRef.current?.pause();
    audioRef.current = null;
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    audioUrlRef.current = null;
    setSpeaking(false);
  }, []);

  const speak = useCallback((text: string) => {
    if (!speechOnRef.current) {
      if (connectedRef.current) window.setTimeout(() => dictationStartRef.current(), 250);
      return;
    }
    speechQueueRef.current.push(text);
    if (pumpingSpeechRef.current) return;
    const generation = speechGenerationRef.current;
    pumpingSpeechRef.current = true;
    void (async () => {
      while (speechQueueRef.current.length && generation === speechGenerationRef.current) {
        try {
          const next = speechQueueRef.current.shift()!;
          const blob = await generateVoiceSpeech(next, voice, voiceSpeed);
          if (generation !== speechGenerationRef.current || !speechOnRef.current) break;
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          audioRef.current = audio;
          audioUrlRef.current = url;
          await new Promise<void>((resolve, reject) => {
            audio.onplay = () => {
              setSpeaking(true);
              if (connectedRef.current) dictationStartRef.current();
            };
            audio.onended = () => resolve();
            audio.onerror = () => reject(new Error("playback failed"));
            void audio.play().catch(reject);
          });
          audio.pause();
          URL.revokeObjectURL(url);
          audioRef.current = null;
          audioUrlRef.current = null;
        } catch {
          if (generation === speechGenerationRef.current) setError("Natural voice playback failed");
        }
      }
      if (generation === speechGenerationRef.current) {
        pumpingSpeechRef.current = false;
        setSpeaking(false);
        if (connectedRef.current) window.setTimeout(() => dictationStartRef.current(), 150);
      }
    })();
  }, [voice, voiceSpeed]);

  // Leaving the page stops any in-flight speech.
  useEffect(
    () => () => {
      audioRef.current?.pause();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    },
    [],
  );

  const handleUtterance = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      // Silence detection and a manual stop can race. Only one voice command
      // may be submitted until the current request has settled.
      if (!trimmed || submittingRef.current) return;
      const sessionGeneration = sessionGenerationRef.current;
      submittingRef.current = true;
      setError(null);
      // History = what the model saw before this utterance.
      const history = turnsRef.current.map(({ role, content }) => ({ role, content }));
      setTurns((prev) => [...prev, { role: "user", content: trimmed }]);
      setThinking(true);
      thinkingRef.current = true;
      try {
        const res = await sendVoiceCommand(trimmed, history, selectedModel);
        if (sessionGeneration !== sessionGenerationRef.current) return;
        setTurns((prev) => [
          ...prev,
          {
            role: "assistant",
            content: res.speech,
            actions: res.actions,
            executed: res.executed,
          },
        ]);
        setThinking(false);
        thinkingRef.current = false;
        speak(res.speech);
        if (res.executed) onBoardChange?.();
      } catch (e) {
        if (sessionGeneration !== sessionGenerationRef.current) return;
        const message = e instanceof Error ? e.message : "Something went wrong";
        setError(message);
        if (connectedRef.current) window.setTimeout(() => dictationStartRef.current(), 250);
      } finally {
        if (sessionGeneration === sessionGenerationRef.current) {
          submittingRef.current = false;
          setThinking(false);
          thinkingRef.current = false;
        }
      }
    },
    [onBoardChange, selectedModel, speak],
  );

  const dictation = useLiveDictation((text) => void handleUtterance(text), () => {
      if (audioRef.current && !audioRef.current.paused) stopSpeech();
  });
  dictationStartRef.current = () => {
    if (!thinkingRef.current && selectedModel && dictation.supported && dictation.state === "idle") {
      void dictation.start();
    }
  };

  // Voice Mode is hands-free by default. Start listening as soon as the
  // persisted model catalog and microphone capability are ready.
  useEffect(() => {
    if (!connected || !selectedModel || !dictation.supported) return;
    const timer = window.setTimeout(() => dictationStartRef.current(), 0);
    return () => window.clearTimeout(timer);
  }, [connected, selectedModel, dictation.supported]);

  // Announce task transitions while this connected session is open.
  useEffect(() => {
    if (!connected) {
      taskStatesRef.current = null;
      return;
    }
    const poll = async () => {
      try {
        const board = await fetchTasksBoard();
        const next = new Map(board.tasks.map((task) => [task.conversation_id, task.column]));
        const previous = taskStatesRef.current;
        taskStatesRef.current = next;
        if (!previous) return;
        const changed = board.tasks.filter((task) => {
          const before = previous.get(task.conversation_id);
          return before && before !== task.column && ["done", "needs_input"].includes(task.column);
        });
        if (changed.length && !thinking) {
          const update = taskChangeSpeech(changed);
          setTurns((current) => [...current, { role: "assistant", content: update }]);
          speak(update);
        }
      } catch {
        // A transient board poll failure should not end the voice session.
      }
    };
    void poll();
    const interval = window.setInterval(poll, 5000);
    return () => window.clearInterval(interval);
  }, [connected, speak, thinking]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, thinking]);

  const micState =
    dictation.state === "recording"
      ? "Listening… tap to stop"
        : dictation.state === "connecting"
          ? "Connecting…"
        : thinking
          ? "Thinking…"
          : speaking
            ? "Loma is speaking… tap the mic to interrupt"
            : connected
              ? "Connected · listening resumes after each reply"
              : "Tap to speak";

  const clearVoiceChat = () => {
    sessionGenerationRef.current += 1;
    submittingRef.current = false;
    thinkingRef.current = false;
    dictation.cancel();
    stopSpeech();
    taskStatesRef.current = null;
    sessionStorage.removeItem(VOICE_HISTORY_KEY);
    setTurns([]);
    setTyped("");
    setThinking(false);
    setError(null);
    if (connectedRef.current) window.setTimeout(() => dictationStartRef.current(), 150);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <RiMicLine size={17} />
          </span>
          <div className="min-w-0">
            <h2 className="truncate font-heading text-sm font-semibold">Voice Mode</h2>
            <p className="truncate text-[11px] text-muted-foreground">Live with your task board</p>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0 gap-1.5 px-2 text-xs"
          onClick={clearVoiceChat}
          aria-label="Clear voice chat and start fresh"
          title="New voice chat"
        >
          <RiRestartLine size={16} />
          <span className="hidden sm:inline">New chat</span>
        </Button>
        <Button
          variant={connected ? "secondary" : "ghost"}
          size="icon"
          className="shrink-0"
          aria-label={connected ? "Disable hands-free mode" : "Enable hands-free mode"}
          disabled={!selectedModel || !dictation.supported}
          onClick={() => {
            const next = !connected;
            setConnected(next);
            if (next) {
              stopSpeech();
              setSpeaking(false);
              window.setTimeout(() => dictationStartRef.current(), 0);
            } else {
              dictation.cancel();
              stopSpeech();
            }
          }}
        >
          <RiRadioButtonLine size={16} className={connected ? "text-emerald-500" : ""} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="shrink-0"
          onClick={() => {
            if (speechOn) stopSpeech();
            setSpeechOn((on) => !on);
          }}
          aria-label={speechOn ? "Mute spoken replies" : "Unmute spoken replies"}
        >
          {speechOn ? <RiVolumeUpLine size={18} /> : <RiVolumeMuteLine size={18} />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="shrink-0"
          onClick={onClose}
          aria-label="Close Voice Mode"
        >
          <RiCloseLine size={18} />
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
              {turn.executed && turn.actions?.map((action, index) => ACTION_LABELS[action.type] && (
                <span key={`${action.type}-${index}`} className="mt-1.5 flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                  <RiCheckboxCircleFill size={14} />
                  {ACTION_LABELS[action.type]}
                </span>
              ))}
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
        <div className="mb-3 flex flex-wrap justify-center gap-2">
          <ModelPicker
            models={models}
            selectedModel={selectedModel}
            onSelect={selectModel}
            loadState={loadState}
            disabled={thinking || dictation.state === "recording"}
          />
          <select
            aria-label="Voice"
            value={voice}
            onChange={(event) => {
              setVoice(event.target.value);
              localStorage.setItem(VOICE_CHOICE_KEY, event.target.value);
            }}
            disabled={speaking}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            {VOICES.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
          <select
            aria-label="Voice speed"
            value={voiceSpeed}
            onChange={(event) => {
              const speed = Number(event.target.value);
              setVoiceSpeed(speed);
              localStorage.setItem(VOICE_SPEED_KEY, String(speed));
            }}
            disabled={speaking}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="1">1.0×</option>
            <option value="1.1">1.1×</option>
            <option value="1.2">1.2×</option>
          </select>
        </div>
        <div className="flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={() => {
              if (speaking) {
                stopSpeech();
                void dictation.start();
                return;
              }
              dictation.toggle();
            }}
            disabled={!selectedModel || !dictation.supported || dictation.state === "connecting" || (thinking && !speaking)}
            aria-label={dictation.state === "recording" ? "Stop recording" : "Start recording"}
            className={cn(
              "flex h-16 w-16 items-center justify-center rounded-full text-white shadow-lg transition-colors press-scale",
              dictation.state === "recording"
                ? "animate-pulse bg-red-500"
                : speaking
                  ? "bg-amber-500"
                : "bg-primary disabled:opacity-40",
            )}
          >
            {dictation.state === "recording" ? <RiStopFill size={26} /> : <RiMicLine size={26} />}
          </button>
          <span className="text-xs text-muted-foreground">
            {dictation.supported ? (
              dictation.state === "recording" ? `${micState.replace("tap to stop", "stops automatically")} · ${dictation.seconds}s` : micState
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

function taskChangeSpeech(tasks: Task[]): string {
  const names = tasks.map((task) => task.title || task.prompt.slice(0, 48) || "A task");
  if (tasks.length === 1) {
    return tasks[0].column === "done"
      ? `${names[0]} just finished.`
      : `${names[0]} needs your input.`;
  }
  return `${tasks.length} tasks changed. ${names.slice(0, 2).join(" and ")} are ready for you.`;
}
