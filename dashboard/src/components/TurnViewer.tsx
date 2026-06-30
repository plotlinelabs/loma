"use client";

import { useState } from "react";
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
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 hover:bg-gray-100 transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <span className="text-brand-700 text-xs font-mono font-medium">
            {call.tool_name}
          </span>
          {result?.is_error && (
            <span className="text-xs text-red-600 bg-red-50 px-1.5 py-0.5 rounded">Error</span>
          )}
        </div>
        <span className="text-gray-400 text-xs">
          {expanded ? "collapse" : "expand"}
        </span>
      </button>
      {expanded && (
        <div className="p-3 space-y-2 text-xs bg-surface">
          <div>
            <div className="text-gray-500 mb-1 font-medium">Input:</div>
            <pre className="bg-gray-50 p-2 rounded-lg overflow-x-auto text-gray-700 max-h-60 overflow-y-auto border border-gray-100">
              {call.input}
            </pre>
          </div>
          {result && (
            <div>
              <div className="text-gray-500 mb-1 font-medium">
                Output{result.is_error ? " (Error)" : ""}:
              </div>
              <pre
                className={`p-2 rounded-lg overflow-x-auto max-h-60 overflow-y-auto border ${
                  result.is_error
                    ? "bg-red-50 text-red-700 border-red-100"
                    : "bg-gray-50 text-gray-700 border-gray-100"
                }`}
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
      <p className="text-gray-400 text-sm italic">No turns recorded.</p>
    );
  }

  // Display turns in reverse chronological order (latest first).
  const reversedTurns = [...turns].reverse();

  return (
    <div className="space-y-3">
      {reversedTurns.map((turn) => {
        const resultMap = new Map(
          (turn.tool_results || []).map((r) => [r.tool_use_id, r])
        );

        return (
          <div
            key={`${turn.conversation_id}-${turn.turn_number}`}
            className="bg-surface border border-gray-200 rounded-xl p-4"
          >
            <div className="flex items-center gap-3 mb-3">
              <span className="text-xs font-mono bg-brand-50 text-brand-700 px-2 py-0.5 rounded-md font-medium">
                Turn {turn.turn_number}
              </span>
              <ClientTimestamp
                iso={turn.timestamp}
                variant="time"
                className="text-xs text-gray-400"
              />
            </div>

            {/* Text blocks */}
            {turn.text_blocks?.map((block, i) => (
              <div
                key={i}
                className="mb-3 text-sm text-gray-700 whitespace-pre-wrap leading-relaxed"
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
