"use client";

import { useEffect, useRef, useState } from "react";
import { type EvalRun, getRun } from "@/lib/prompt-eval-api";

const POLL_INTERVAL_MS = 2000;
// "incomplete" is a real distinct outcome (not "failed"): the worker pool
// finalized this run but fewer cases landed than were supposed to run — see
// eval/worker.py's finalization check against total_cases.
const TERMINAL_STATUSES = new Set(["completed", "failed", "incomplete"]);

// Polls GET /runs/{run_id} while a run is pending/running, stops once it
// hits a terminal status. Call start(initialRun) with the pending doc
// handle_run_suite's 202 response returns — polling begins immediately from
// there, so partial case_results show up as they land instead of waiting
// for "completed".
export function useEvalRunPolling() {
  const [run, setRun] = useState<EvalRun | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = () => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  };

  useEffect(() => stop, []); // clear on unmount

  const start = (initialRun: EvalRun) => {
    stop();
    setRun(initialRun);
    if (TERMINAL_STATUSES.has(initialRun.status)) return;

    intervalRef.current = setInterval(async () => {
      try {
        const latest = await getRun(initialRun.run_id);
        setRun(latest);
        if (TERMINAL_STATUSES.has(latest.status)) stop();
      } catch {
        // transient fetch failure — keep polling, don't stop on one bad tick
      }
    }, POLL_INTERVAL_MS);
  };

  const reset = () => {
    stop();
    setRun(null);
  };

  return { run, start, reset, isPolling: intervalRef.current !== null };
}
