"use client";

import type { ReactNode } from "react";
import { RiEqualizerLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

/** Reuse the existing Agent/Tools/Skills pickers in one phone-only settings sheet. */
export function ComposerSettings({ children }: { children: ReactNode }) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button type="button" variant="ghost" size="icon" aria-label="Chat settings" className="size-11 shrink-0 rounded-full text-muted-foreground">
          <RiEqualizerLine size={18} />
        </Button>
      </SheetTrigger>
      <SheetContent side="bottom" aria-describedby={undefined} className="rounded-t-2xl p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))]">
        <SheetTitle>Chat settings</SheetTitle>
        <p className="text-muted-foreground">Choose your agent, tools and skills.</p>
        <div className="flex flex-wrap items-center gap-3 [&_button]:min-h-11">{children}</div>
      </SheetContent>
    </Sheet>
  );
}
