"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RiArrowGoBackLine, RiHome4Line, RiHandHeartLine, RiSendPlaneLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchDeviceScreen, takeover, takeoverInput } from "@/lib/devices-api";

const FRAME_MS = 1000;

/**
 * Live view of a device (about one frame per second while visible). "Take over" pauses any agent
 * session on the device: its calls fail with a clear message until you hand it back, so you can
 * get past a login/OTP or drive a repro yourself. Clicks on the screen become taps; drags become swipes.
 */
export default function DeviceLiveView({ deviceId, platform }: { deviceId: string; platform: string }) {
  const [frame, setFrame] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [held, setHeld] = useState(false);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const drag = useRef<{ fx: number; fy: number } | null>(null);
  const frameRef = useRef<string | null>(null);

  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    const tick = async () => {
      while (live) {
        if (!document.hidden) {
          try {
            const url = await fetchDeviceScreen(deviceId, controller.signal);
            if (!live) {
              URL.revokeObjectURL(url);
              break;
            }
            if (frameRef.current) URL.revokeObjectURL(frameRef.current);
            frameRef.current = url;
            setFrame(url);
            setError(null);
          } catch (e) {
            const status = (e as { status?: number }).status;
            if (live && status !== 429 && !controller.signal.aborted) {
              setError(e instanceof Error ? e.message : "No frame");
            }
          }
        }
        await new Promise((resolve) => setTimeout(resolve, FRAME_MS));
      }
    };
    tick();
    return () => {
      live = false;
      controller.abort();
      if (frameRef.current) URL.revokeObjectURL(frameRef.current);
      frameRef.current = null;
    };
  }, [deviceId]);

  // Hand the device back if the view closes while holding it.
  const heldRef = useRef(false);
  heldRef.current = held;
  useEffect(
    () => () => {
      if (heldRef.current) takeover(deviceId, "end").catch(() => undefined);
    },
    [deviceId],
  );

  const act = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }, []);

  const toggleHold = () =>
    act(async () => {
      const result = await takeover(deviceId, held ? "end" : "start");
      setHeld(result.held);
    });

  const fraction = (event: React.MouseEvent<HTMLImageElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      fx: Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
      fy: Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1),
    };
  };

  const onMouseUp = (event: React.MouseEvent<HTMLImageElement>) => {
    const start = drag.current;
    drag.current = null;
    if (!held || !start || busy) return;
    const end = fraction(event);
    const moved = Math.hypot(end.fx - start.fx, end.fy - start.fy) > 0.03;
    act(() =>
      moved
        ? takeoverInput(deviceId, "swipe", { fx1: start.fx, fy1: start.fy, fx2: end.fx, fy2: end.fy })
        : takeoverInput(deviceId, "tap", end),
    );
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant={held ? "default" : "outline"} className="h-7 text-xs" disabled={busy} onClick={toggleHold}>
          <RiHandHeartLine size={14} />
          {held ? "Hand back to agent" : "Take over"}
        </Button>
        {held && (
          <>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={busy}
              onClick={() => act(() => takeoverInput(deviceId, "key", { key: platform === "ios" ? "home" : "back" }))}>
              {platform === "ios" ? <RiHome4Line size={14} /> : <RiArrowGoBackLine size={14} />}
              {platform === "ios" ? "Home" : "Back"}
            </Button>
            {platform !== "ios" && (
              <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" disabled={busy}
                onClick={() => act(() => takeoverInput(deviceId, "key", { key: "home" }))}>
                <RiHome4Line size={14} />
                Home
              </Button>
            )}
            <div className="flex items-center gap-1">
              <Input
                className="h-7 w-44 text-xs"
                value={text}
                placeholder="Type into the focused field"
                maxLength={500}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && text && !busy && !e.nativeEvent.isComposing) {
                    act(() => takeoverInput(deviceId, "type", { text })).then(() => setText(""));
                  }
                }}
              />
              <Button size="sm" variant="ghost" className="h-7 w-7 p-0" aria-label="Send text" disabled={busy || !text}
                onClick={() => act(() => takeoverInput(deviceId, "type", { text })).then(() => setText(""))}>
                <RiSendPlaneLine size={14} />
              </Button>
            </div>
          </>
        )}
      </div>
      <div className="text-[11px] text-muted-foreground">
        {held
          ? "You have the device: the agent's calls on it wait until you hand it back. Click to tap, drag to swipe."
          : "Live view (about one frame per second). Take over to tap and type; the agent pauses meanwhile."}
      </div>
      {error && <div className="text-xs text-red-500">{error}</div>}
      {frame ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={frame}
          alt="Live device screen"
          draggable={false}
          className={`max-h-[560px] w-auto rounded-md border select-none ${held ? "cursor-crosshair" : ""}`}
          onMouseDown={(e) => {
            drag.current = held ? fraction(e) : null;
          }}
          onMouseUp={onMouseUp}
        />
      ) : (
        !error && <div className="text-xs text-muted-foreground">Connecting to the device...</div>
      )}
    </div>
  );
}
