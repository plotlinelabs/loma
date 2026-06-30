"use client";

import { useState } from "react";
import { RiArrowDownSLine, RiArrowRightSLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Turn } from "../lib/api";
import ClientTimestamp from "./ClientTimestamp";

interface TurnViewerProps {
  turns: Turn[];
}

function ToolCallCard({
  call,
  result,
}: {
  call: Turn["tool_calls"] extends (infer T)[] | undefined ? T : never;
  result?: Turn["tool_results"] extends (infer T)[] | undefined ? T : never;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <Button
        variant="ghost"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 h-auto bg-muted/50 hover:bg-muted transition-colors text-left rounded-none"
      >
        <div className="flex items-center gap-2">
          <span className="text-brand-700 text-xs font-mono font-medium">
            {call.tool_name}
          </span>
          {result?.is_error && (
            <Badge variant="destructive" className="text-xs">Error</Badge>
          )}
        </div>
        <span className="text-muted-foreground text-xs flex items-center">
          {expanded ? (
            <RiArrowDownSLine size={14} />
          ) : (
            <RiArrowRightSLine size={14} />
          )}
        </span>
      </Button>
      {expanded && (
        <div className="p-3 space-y-2 text-xs bg-card">
          <div>
            <div className="text-muted-foreground mb-1 font-medium">Input:</div>
            <pre className="bg-muted/50 p-2 rounded-lg overflow-x-auto text-foreground/70 max-h-60 overflow-y-auto border border-border/50">
              {call.input}
            </pre>
          </div>
          {result && (
            <div>
              <div className="text-muted-foreground mb-1 font-medium">
                Output{result.is_error ? " (Error)" : ""}:
              </div>
              <pre
                className={cn(
                  "p-2 rounded-lg overflow-x-auto max-h-60 overflow-y-auto border",
                  result.is_error
                    ? "bg-red-50 text-red-700 border-red-100"
                    : "bg-muted/50 text-foreground/70 border-border/50"
                )}
              >
                {result.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function TurnViewer({ turns }: TurnViewerProps) {
  if (!turns.length) {
    return (
      <p className="text-muted-foreground text-sm italic">No turns recorded.</p>
    );
  }

  // Display turns in reverse chronological order (latest first).
  const reversedTurns = [...turns].reverse();

  return (
    <div className="space-y-2">
      {reversedTurns.map((turn) => {
        const resultMap = new Map(
          (turn.tool_results || []).map((r) => [r.tool_use_id, r])
        );

        return (
          <div
            key={`${turn.conversation_id}-${turn.turn_number}`}
            className="bg-card border border-border rounded-xl p-3"
          >
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="secondary" className="font-mono text-xs bg-brand-50 text-brand-700">
                Turn {turn.turn_number}
              </Badge>
              <ClientTimestamp
                iso={turn.timestamp}
                variant="time"
                className="text-xs text-muted-foreground"
              />
            </div>

            {/* Text blocks */}
            {turn.text_blocks?.map((block, i) => (
              <div
                key={i}
                className="mb-2 text-sm text-foreground/70 whitespace-pre-wrap leading-relaxed"
              >
                {block.text}
              </div>
            ))}

            {/* Tool calls */}
            {turn.tool_calls && turn.tool_calls.length > 0 && (
              <div className="space-y-2 mt-2">
                {turn.tool_calls.map((call, i) => (
                  <ToolCallCard
                    key={i}
                    call={call}
                    result={resultMap.get(call.tool_use_id)}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
