"use client";

import { useEffect, useId, useState } from "react";
import { RiFullscreenLine, RiLoader4Line } from "@remixicon/react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

let initialized: Promise<typeof import("mermaid").default> | null = null;

/** Mermaid, loaded once. "strict" sanitizes labels and disables click handlers,
 * so agent-written diagrams can't run script in the page. */
function loadMermaid() {
  initialized ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      fontFamily: "ui-sans-serif, system-ui, sans-serif",
    });
    return mermaid;
  });
  return initialized;
}

/** A Mermaid diagram drawn inline; click to see it full screen. Falls back to
 * the source with a note when it doesn't parse. */
export function MermaidDiagram({ source }: { source: string }) {
  const id = `mmd-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadMermaid()
      .then(async (mermaid) => {
        // parse first: a failed render() leaves an error graphic in <body>.
        if (!(await mermaid.parse(source, { suppressErrors: true }))) throw new Error("invalid");
        const { svg: drawn } = await mermaid.render(id, source);
        if (!cancelled) setSvg(drawn);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [id, source]);

  if (failed) {
    return (
      <figure className="not-prose my-4">
        <pre className="overflow-x-auto rounded-lg bg-muted p-3 text-xs leading-relaxed">{source}</pre>
        <figcaption className="mt-1 text-[12px] text-muted-foreground">This diagram couldn&apos;t be drawn, so here is its source.</figcaption>
      </figure>
    );
  }
  if (!svg) {
    return (
      <div className="not-prose my-4 flex h-32 items-center justify-center gap-2 rounded-xl border border-border text-[13px] text-muted-foreground">
        <RiLoader4Line size={16} className="animate-spin" /> Drawing diagram…
      </div>
    );
  }
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open diagram full screen"
        className="not-prose group/diagram relative my-4 block w-full overflow-x-auto rounded-xl border border-border bg-white p-4 text-left transition-colors hover:border-foreground/20 dark:bg-neutral-50"
      >
        <span className="flex justify-center [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />
        <RiFullscreenLine size={14} className="absolute right-2 top-2 text-muted-foreground opacity-0 transition-opacity group-hover/diagram:opacity-100" />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] max-w-[95vw] overflow-auto bg-white sm:max-w-[95vw]">
          <DialogTitle className="sr-only">Diagram</DialogTitle>
          <div className="flex justify-center [&_svg]:h-auto [&_svg]:min-w-[60vw] [&_svg]:max-w-none" dangerouslySetInnerHTML={{ __html: svg }} />
        </DialogContent>
      </Dialog>
    </>
  );
}
