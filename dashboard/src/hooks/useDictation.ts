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
interface DictationOptions {
  autoStop?: boolean;
  silenceMs?: number;
  onSpeechStart?: () => void;
}

export function useDictation(onText: (text: string) => void, options: DictationOptions = {}) {
  const [state, setState] = useState<DictationState>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // Feature-detect after mount — SSR must render the same "no mic" markup
  // as the client's first pass or hydration fails.
  const [supported, setSupported] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const discardRef = useRef(false);
  const analyserFrameRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  useEffect(() => {
    setSupported(
      typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia,
    );
  }, []);

  const cleanup = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
    if (analyserFrameRef.current) cancelAnimationFrame(analyserFrameRef.current);
    analyserFrameRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
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
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
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

    if (optionsRef.current.autoStop) {
      const AudioContextClass = window.AudioContext;
      const context = new AudioContextClass();
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      context.createMediaStreamSource(stream).connect(analyser);
      audioContextRef.current = context;
      const samples = new Uint8Array(analyser.fftSize);
      const startedAt = performance.now();
      let noiseFloor = 0.008;
      let voicedSince: number | null = null;
      let lastVoiceAt: number | null = null;
      let announced = false;
      const monitor = () => {
        if (recorder.state !== "recording") return;
        analyser.getByteTimeDomainData(samples);
        let sum = 0;
        for (const sample of samples) {
          const value = (sample - 128) / 128;
          sum += value * value;
        }
        const rms = Math.sqrt(sum / samples.length);
        const now = performance.now();
        if (now - startedAt < 350) noiseFloor = Math.max(noiseFloor, rms);
        const isVoice = rms > Math.max(0.025, noiseFloor * 2.8);
        if (isVoice) {
          voicedSince ??= now;
          lastVoiceAt = now;
          if (!announced && now - voicedSince >= 180) {
            announced = true;
            optionsRef.current.onSpeechStart?.();
          }
        } else {
          voicedSince = null;
        }
        if (announced && lastVoiceAt && now - lastVoiceAt >= (optionsRef.current.silenceMs ?? 1200)) {
          recorder.stop();
          return;
        }
        analyserFrameRef.current = requestAnimationFrame(monitor);
      };
      analyserFrameRef.current = requestAnimationFrame(monitor);
    }
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

  return { state, seconds, error, supported, start, toggle, cancel };
}
