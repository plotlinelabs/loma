"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { transcribeAudio } from "@/lib/api";

/** Recording formats in preference order — Chrome/Firefox produce Opus webm,
 * iOS/macOS Safari only support AAC mp4. Both upload fine to the backend. */
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

const MAX_SECONDS = 180; // matches the backend size cap with headroom

export type DictationState = "idle" | "recording" | "transcribing";

/** Record mic audio with MediaRecorder and turn it into text via the
 * backend's /api/transcribe. Toggle semantics: first call starts recording,
 * second stops it and fires `onText` with the transcript. */
export function useDictation(onText: (text: string) => void) {
  const [state, setState] = useState<DictationState>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const discardRef = useRef(false);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const supported =
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia;

  const cleanup = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  // Unmount mid-recording: kill the mic, drop the audio.
  useEffect(
    () => () => {
      discardRef.current = true;
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      cleanup();
    },
    [cleanup],
  );

  const start = useCallback(async () => {
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone access denied");
      return;
    }
    const mimeType = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m));
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = async () => {
      const type = recorder.mimeType || mimeType || "audio/webm";
      cleanup();
      if (discardRef.current) {
        setState("idle");
        return;
      }
      const blob = new Blob(chunks, { type });
      // A tap-and-immediate-stop is just container headers — skip the API call.
      if (blob.size < 2048) {
        setState("idle");
        return;
      }
      setState("transcribing");
      try {
        const text = await transcribeAudio(blob, type.includes("mp4") ? "recording.mp4" : "recording.webm");
        if (text) onTextRef.current(text);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Transcription failed");
      } finally {
        setState("idle");
      }
    };

    discardRef.current = false;
    streamRef.current = stream;
    recorderRef.current = recorder;
    recorder.start();
    setSeconds(0);
    setState("recording");
    timerRef.current = setInterval(() => {
      setSeconds((s) => {
        // Hard cap — stop and transcribe what we have.
        if (s + 1 >= MAX_SECONDS && recorderRef.current?.state === "recording") {
          recorderRef.current.stop();
        }
        return s + 1;
      });
    }, 1000);
  }, [cleanup]);

  const toggle = useCallback(() => {
    if (state === "transcribing") return;
    if (state === "recording") {
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      return;
    }
    void start();
  }, [state, start]);

  /** Stop and throw away the recording (no transcription). */
  const cancel = useCallback(() => {
    if (state !== "recording") return;
    discardRef.current = true;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    setState("idle");
  }, [state]);

  return { state, seconds, error, supported, toggle, cancel };
}
