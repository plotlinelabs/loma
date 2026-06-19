"use client";

import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export default function WebTerminal({
  className = "",
  autoCommand,
  tokenEndpoint = "/api/terminal/token",
}: {
  className?: string;
  autoCommand?: string;
  tokenEndpoint?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, monospace",
      theme: {
        background: "#1a1b26",
        foreground: "#a9b1d6",
        cursor: "#c0caf5",
        selectionBackground: "#33467c",
        black: "#15161e",
        red: "#f7768e",
        green: "#9ece6a",
        yellow: "#e0af68",
        blue: "#7aa2f7",
        magenta: "#bb9af7",
        cyan: "#7dcfff",
        white: "#a9b1d6",
        brightBlack: "#414868",
        brightRed: "#f7768e",
        brightGreen: "#9ece6a",
        brightYellow: "#e0af68",
        brightBlue: "#7aa2f7",
        brightMagenta: "#bb9af7",
        brightCyan: "#7dcfff",
        brightWhite: "#c0caf5",
      },
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();

    termRef.current = term;
    fitRef.current = fit;

    let cancelled = false;

    async function connect() {
      try {
        // Fetch a one-time token via the authenticated API
        const res = await fetch(`${API_BASE}${tokenEndpoint}`, { method: "POST" });
        if (!res.ok) {
          throw new Error(`Token request failed: ${res.status}`);
        }
        const { token } = await res.json();
        if (cancelled) return;

        // Connect WebSocket with the token
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${proto}//${window.location.host}/api/terminal/ws?token=${encodeURIComponent(token)}`;
        const ws = new WebSocket(wsUrl);
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;

        ws.onopen = () => {
          if (cancelled) { ws.close(); return; }
          setStatus("connected");
          const dims = fit.proposeDimensions();
          if (dims) {
            ws.send(`\x01RESIZE:${dims.cols},${dims.rows}`);
          }
          // Auto-run a command if provided (e.g., claude login)
          if (autoCommand) {
            setTimeout(() => ws.send(autoCommand + "\n"), 500);
          }
        };

        ws.onmessage = (event) => {
          if (event.data instanceof ArrayBuffer) {
            term.write(new Uint8Array(event.data));
          } else {
            term.write(event.data);
          }
        };

        ws.onclose = () => setStatus("disconnected");
        ws.onerror = () => setStatus("disconnected");
      } catch {
        if (!cancelled) setStatus("disconnected");
      }
    }

    connect();

    // Forward keystrokes to the server
    term.onData((data) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });

    // Handle resize
    const resizeObserver = new ResizeObserver(() => {
      fit.fit();
      const dims = fit.proposeDimensions();
      const ws = wsRef.current;
      if (dims && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(`\x01RESIZE:${dims.cols},${dims.rows}`);
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      cancelled = true;
      resizeObserver.disconnect();
      wsRef.current?.close();
      term.dispose();
    };
  }, []);

  const reconnect = async () => {
    setStatus("connecting");
    // Force remount by toggling key — simplest approach
    window.location.reload();
  };

  return (
    <div className={className}>
      <div className="flex items-center gap-2 px-3 py-2 bg-[#1a1b26] rounded-t-xl border border-b-0 border-gray-700">
        <div className={`w-2 h-2 rounded-full ${
          status === "connected" ? "bg-emerald-500" :
          status === "connecting" ? "bg-amber-500 animate-pulse" :
          "bg-red-500"
        }`} />
        <span className="text-xs text-gray-400 font-mono">
          {status === "connected" ? "Terminal" :
           status === "connecting" ? "Connecting..." :
           "Disconnected"}
        </span>
        {status === "disconnected" && (
          <button
            onClick={reconnect}
            className="ml-auto text-xs text-blue-400 hover:text-blue-300"
          >
            Reconnect
          </button>
        )}
      </div>
      <div
        ref={containerRef}
        className="rounded-b-xl border border-gray-700 overflow-hidden"
        style={{ height: 350, background: "#1a1b26" }}
      />
    </div>
  );
}
