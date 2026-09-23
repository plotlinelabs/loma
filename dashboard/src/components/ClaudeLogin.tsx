"use client";

import { useEffect, useRef, useState } from "react";
import { claudeLoginRequest, type ClaudeLoginSession } from "../lib/claude-auth-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";

export default function ClaudeLogin({ sessionId, onConnected }: { sessionId: string; onConnected: () => void }) {
  const done = useRef(false);
  const cancelTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callback = useRef(onConnected);
  const [session, setSession] = useState<ClaudeLoginSession | null>(null);
  const [code, setCode] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { callback.current = onConnected; }, [onConnected]);

  useEffect(() => {
    // React Strict Mode replays effects in development; defer cancellation so
    // a replay does not destroy the just-created server session.
    if (cancelTimer.current) clearTimeout(cancelTimer.current);
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await claudeLoginRequest(`/${sessionId}`);
        if (stopped) return;
        setSession(result);
        if (result.state === "connected") {
          done.current = true;
          callback.current();
          return;
        }
        if (result.state === "failed" || result.state === "cancelled") {
          setError(result.error || "Login cancelled. Close this dialog and try again.");
          return;
        }
        timer = setTimeout(poll, 1500);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : "Login session ended.");
      }
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      if (!done.current) cancelTimer.current = setTimeout(() => {
        void claudeLoginRequest(`/${sessionId}`, "DELETE").catch(() => {});
      }, 0);
    };
  }, [sessionId]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    try {
      await claudeLoginRequest(`/${sessionId}/code`, "POST", code.trim());
      setCode("");
    } catch (e) {
      setSubmitted(false);
      setError(e instanceof Error ? e.message : "Could not submit code.");
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        This connection joins your organization’s shared Claude pool. Tasks from any user
        rotate across available connections. Personal Google and Slack access stays with the task owner.
      </p>
      {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : (
        <>
          {session?.url ? (
            <a className="text-sm text-primary underline" href={session.url} target="_blank" rel="noopener noreferrer">
              Open Anthropic sign-in
            </a>
          ) : <p className="text-sm text-muted-foreground" role="status">Starting secure login sandbox...</p>}
          <form onSubmit={submit} className="space-y-3">
            <Label htmlFor="claude-login-code">Authorization code</Label>
            <Input id="claude-login-code" type="password" autoComplete="off" value={code}
              onChange={(e) => setCode(e.target.value)} placeholder="Paste the code from Anthropic"
              disabled={!session?.url || submitted} maxLength={2048} />
            <Button type="submit" disabled={!session?.url || !code.trim() || submitted}>
              {submitted ? "Finishing login..." : "Complete login"}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground">Expires after 10 minutes. Closing this dialog cancels login. No host terminal is opened.</p>
        </>
      )}
    </div>
  );
}
