"use client";

import { useRef, type ComponentProps } from "react";
import { CopyButton } from "./CopyButton";

/** react-markdown `pre` with a copy button, for documents and plans in the side panel. */
export function CopyablePre(allProps: ComponentProps<"pre"> & { node?: unknown }) {
  // react-markdown passes its AST `node`; it must not reach the DOM.
  const { children, node, ...props } = allProps;
  void node;
  const ref = useRef<HTMLPreElement>(null);
  return (
    <div className="not-prose group/code relative my-3">
      <pre ref={ref} {...props} className="!my-0 overflow-x-auto rounded-lg bg-[#0A0A0A] p-3 pr-10 text-xs leading-relaxed text-[#E8E4DD]">
        {children}
      </pre>
      <CopyButton
        getText={() => ref.current?.innerText ?? ""}
        label="Copy code"
        className="absolute right-1.5 top-1.5 h-6 w-6 text-[#8C857D] opacity-0 transition-opacity hover:bg-white/10 hover:text-[#E8E4DD] group-hover/code:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100"
      />
    </div>
  );
}

/** Pass as react-markdown `components` to give fenced code a copy button. */
export const markdownComponents = { pre: CopyablePre };
