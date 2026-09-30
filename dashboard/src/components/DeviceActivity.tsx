"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";
import ClientTimestamp from "@/components/ClientTimestamp";
import { DeviceActivityEvent, DeviceSession, fetchDeviceActivity } from "@/lib/devices-api";

function describe(event: DeviceActivityEvent): string {
  const detail = event.detail || {};
  const parts = Object.entries(detail).map(([key, value]) =>
    typeof value === "object" ? `${key}=${JSON.stringify(value)}` : `${key}=${String(value)}`,
  );
  return parts.join(" ");
}

/**
 * Session timeline for one device, built from the device audit log: one block per
 * lease scope (usually a conversation), newest first, with every operation it ran.
 */
export default function DeviceActivity({ deviceId }: { deviceId: string }) {
  const [data, setData] = useState<{ sessions: DeviceSession[]; events: DeviceActivityEvent[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchDeviceActivity(deviceId)
      .then((result) => live && setData(result))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load activity"));
    return () => {
      live = false;
    };
  }, [deviceId]);

  if (error) return <div className="text-xs text-red-500">{error}</div>;
  if (!data) return <Skeleton className="h-16 w-full" />;
  if (data.events.length === 0) {
    return <div className="text-xs text-muted-foreground">No activity in the last 90 days.</div>;
  }
  return (
    <div className="space-y-2">
      {data.sessions.map((session) => {
        const events = data.events.filter((e) => e.scope === session.scope).slice().reverse();
        return (
          <div key={session.scope} className="rounded-md border px-3 py-2 space-y-1">
            <div className="text-xs text-foreground flex flex-wrap items-center gap-x-2">
              <span className="font-medium">{session.actor}</span>
              <span className="text-muted-foreground">
                <ClientTimestamp iso={session.started_at} variant="short" /> · {session.ops} ops
                {session.failures ? ` · ${session.failures} failed` : ""}
              </span>
              {session.conversation_id && (
                <Link className="underline text-muted-foreground" href={`/chat?continue=${session.conversation_id}`}>
                  conversation
                </Link>
              )}
            </div>
            <ol className="space-y-0.5">
              {events.map((event, index) => (
                <li key={`${event.at}-${index}`} className="text-[11px] font-mono flex gap-2">
                  <span className="text-muted-foreground shrink-0">
                    {new Date(event.at).toLocaleTimeString()}
                  </span>
                  <span className={event.ok ? "text-foreground" : "text-red-500"}>{event.op}</span>
                  <span className="text-muted-foreground truncate">
                    {describe(event)}
                    {event.duration_ms !== undefined ? ` (${event.duration_ms} ms)` : ""}
                    {event.error ? ` · ${event.error}` : ""}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        );
      })}
    </div>
  );
}
