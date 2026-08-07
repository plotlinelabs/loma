"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createVoiceListenToken, voiceListenWebSocketUrl } from "@/lib/api";

export type LiveDictationState = "idle" | "connecting" | "recording";

export function useLiveDictation(
  onText: (text: string) => void,
  onSpeechStart?: () => void,
) {
  const [state, setState] = useState<LiveDictationState>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startingRef = useRef(false);
  const transcriptRef = useRef("");
  const onTextRef = useRef(onText);
  const onSpeechStartRef = useRef(onSpeechStart);

  useEffect(() => {
    onTextRef.current = onText;
    onSpeechStartRef.current = onSpeechStart;
  }, [onText, onSpeechStart]);

  useEffect(() => {
    const available = typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof WebSocket !== "undefined";
    queueMicrotask(() => setSupported(available));
  }, []);

  const cleanup = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send("close");
    socketRef.current?.close();
    socketRef.current = null;
    startingRef.current = false;
    setState("idle");
  }, []);

  const submit = useCallback(() => {
    const text = transcriptRef.current.trim();
    transcriptRef.current = "";
    cleanup();
    if (text) onTextRef.current(text);
  }, [cleanup]);

  const start = useCallback(async () => {
    if (startingRef.current || recorderRef.current || socketRef.current) return;
    startingRef.current = true;
    setState("connecting");
    setError(null);
    transcriptRef.current = "";
    try {
      const [token, stream] = await Promise.all([
        createVoiceListenToken(),
        navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }),
      ]);
      streamRef.current = stream;
      const socket = new WebSocket(voiceListenWebSocketUrl(token));
      socketRef.current = socket;
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.type === "SpeechStarted") {
          onSpeechStartRef.current?.();
          return;
        }
        if (message.type === "Results") {
          const text = message.channel?.alternatives?.[0]?.transcript?.trim();
          if (text && message.is_final) transcriptRef.current = `${transcriptRef.current} ${text}`.trim();
          if (message.speech_final && transcriptRef.current) submit();
          return;
        }
        if (message.type === "UtteranceEnd" && transcriptRef.current) submit();
        if (message.type === "Error") setError(message.description || "Live transcription failed");
      };
      socket.onerror = () => {
        setError("Live transcription connection failed");
        cleanup();
      };
      socket.onopen = () => {
        const mimeType = ["audio/webm;codecs=opus", "audio/webm"].find((type) => MediaRecorder.isTypeSupported(type));
        const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
        recorderRef.current = recorder;
        recorder.ondataavailable = (event) => {
          if (event.data.size && socket.readyState === WebSocket.OPEN) socket.send(event.data);
        };
        recorder.start(250);
        startingRef.current = false;
        setSeconds(0);
        setState("recording");
        timerRef.current = setInterval(() => setSeconds((value) => value + 1), 1000);
      };
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Microphone access denied");
      cleanup();
    }
  }, [cleanup, submit]);

  const cancel = useCallback(() => {
    transcriptRef.current = "";
    cleanup();
  }, [cleanup]);

  const toggle = useCallback(() => {
    if (state === "idle") void start();
    else if (transcriptRef.current) submit();
    else cancel();
  }, [cancel, start, state, submit]);

  useEffect(() => cleanup, [cleanup]);
  return { state, seconds, error, supported, start, toggle, cancel };
}
