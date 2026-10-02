"use client";

import type { ReactNode } from "react";
import { RiAddLine, RiAttachmentLine, RiEqualizerLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

interface ComposerSettingsProps {
  children: ReactNode;
  title?: string;
  description?: string;
  /** "plus" is the one-row phone composer's single entry point: attach,
   * model and settings all sit behind it. */
  trigger?: "settings" | "plus";
  /** Adds an "Attach files" action to the sheet (closes the sheet first). */
  onAttach?: () => void;
}

/** Reuse the existing Agent/Tools/Skills pickers in one phone-only settings sheet. */
export function ComposerSettings({
  children,
  title = "Chat settings",
  description = "Choose your agent, tools and skills.",
  trigger = "settings",
  onAttach,
}: ComposerSettingsProps) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button type="button" variant="ghost" size="icon" aria-label={title} className="size-11 shrink-0 rounded-full text-muted-foreground">
          {trigger === "plus" ? <RiAddLine size={22} /> : <RiEqualizerLine size={18} />}
        </Button>
      </SheetTrigger>
      <SheetContent side="bottom" aria-describedby={undefined} className="rounded-t-2xl p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))]">
        <SheetTitle>{title}</SheetTitle>
        <p className="text-muted-foreground">{description}</p>
        <div className="flex flex-wrap items-center gap-3 [&_button]:min-h-11">
          {onAttach && (
            <SheetClose asChild>
              <Button type="button" variant="outline" onClick={onAttach} className="rounded-xl px-3">
                <RiAttachmentLine size={18} /> Attach files
              </Button>
            </SheetClose>
          )}
          {children}
        </div>
      </SheetContent>
    </Sheet>
  );
}
