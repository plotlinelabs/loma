"use client";

/* ── Diff Viewer ─────────────────────────────────────────────────── */

export interface DiffLine {
  type: "add" | "remove" | "context" | "hunk" | "header";
  content: string;
  oldNum?: number;
  newNum?: number;
}

export function parseDiff(raw: string): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;

  for (const line of raw.split("\n")) {
    if (line.startsWith("diff --git") || line.startsWith("index ") || line.startsWith("---") || line.startsWith("+++")) {
      lines.push({ type: "header", content: line });
    } else if (line.startsWith("@@")) {
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)/);
      if (match) {
        oldLine = parseInt(match[1], 10);
        newLine = parseInt(match[2], 10);
      }
      lines.push({ type: "hunk", content: line });
    } else if (line.startsWith("+")) {
      lines.push({ type: "add", content: line.slice(1), newNum: newLine });
      newLine++;
    } else if (line.startsWith("-")) {
      lines.push({ type: "remove", content: line.slice(1), oldNum: oldLine });
      oldLine++;
    } else {
      const text = line.startsWith(" ") ? line.slice(1) : line;
      if (line.length > 0 || lines.length > 0) {
        lines.push({ type: "context", content: text, oldNum: oldLine, newNum: newLine });
        oldLine++;
        newLine++;
      }
    }
  }
  return lines;
}

export function DiffViewer({ diff, loading }: { diff: string; loading: boolean }) {
  if (loading) {
    return (
      <div className="space-y-1">
        {Array.from({ length: 12 }).map((_, i) => (
          <div key={i} className="skeleton h-5 rounded" style={{ width: `${60 + Math.random() * 40}%` }} />
        ))}
      </div>
    );
  }

  if (!diff.trim()) {
    return (
      <div className="flex items-center justify-center py-12 text-gray-400">
        <div className="text-center">
          <svg className="w-8 h-8 mx-auto mb-2 text-gray-300" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
          </svg>
          <p className="text-sm font-medium">No differences</p>
          <p className="text-xs mt-0.5">These versions are identical.</p>
        </div>
      </div>
    );
  }

  const lines = parseDiff(diff);
  const stats = lines.reduce(
    (acc, l) => {
      if (l.type === "add") acc.add++;
      if (l.type === "remove") acc.del++;
      return acc;
    },
    { add: 0, del: 0 },
  );

  return (
    <div>
      {/* Stats bar */}
      <div className="flex items-center gap-3 mb-3 text-xs">
        <span className="text-green-600 font-semibold">+{stats.add}</span>
        <span className="text-red-500 font-semibold">-{stats.del}</span>
        <div className="flex gap-px flex-1 max-w-[120px]">
          {stats.add > 0 && (
            <div
              className="h-2 rounded-l bg-green-400"
              style={{ flex: stats.add }}
            />
          )}
          {stats.del > 0 && (
            <div
              className="h-2 rounded-r bg-red-400"
              style={{ flex: stats.del }}
            />
          )}
        </div>
      </div>

      {/* Diff lines */}
      <div className="rounded-lg border border-gray-200 overflow-hidden font-mono text-[11px] leading-5 max-h-[600px] overflow-y-auto">
        {lines.map((line, i) => {
          if (line.type === "header") return null;

          const bg =
            line.type === "add"
              ? "bg-green-50"
              : line.type === "remove"
                ? "bg-red-50"
                : line.type === "hunk"
                  ? "bg-blue-50"
                  : "bg-surface";

          const textColor =
            line.type === "add"
              ? "text-green-800"
              : line.type === "remove"
                ? "text-red-700"
                : line.type === "hunk"
                  ? "text-blue-600"
                  : "text-gray-600";

          const prefix =
            line.type === "add"
              ? "+"
              : line.type === "remove"
                ? "-"
                : line.type === "hunk"
                  ? ""
                  : " ";

          return (
            <div key={i} className={`flex ${bg} border-b border-gray-100 last:border-b-0`}>
              {/* Line numbers */}
              {line.type !== "hunk" ? (
                <>
                  <span className="w-10 text-right pr-2 text-gray-300 select-none flex-shrink-0 border-r border-gray-100 bg-gray-50/50">
                    {line.type !== "add" ? line.oldNum ?? "" : ""}
                  </span>
                  <span className="w-10 text-right pr-2 text-gray-300 select-none flex-shrink-0 border-r border-gray-100 bg-gray-50/50">
                    {line.type !== "remove" ? line.newNum ?? "" : ""}
                  </span>
                </>
              ) : (
                <span className="w-20 flex-shrink-0 border-r border-gray-100 bg-blue-50" />
              )}

              {/* Prefix + content */}
              <span className={`w-5 text-center flex-shrink-0 select-none font-bold ${textColor}`}>
                {prefix}
              </span>
              <span className={`flex-1 pr-3 whitespace-pre-wrap break-all ${textColor}`}>
                {line.content}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
