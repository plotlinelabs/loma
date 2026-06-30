import Link from "next/link";
import { RiAddLine, RiBookOpenLine } from "@remixicon/react";

export default function SkillEmptyState({ createUrl }: { createUrl: string }) {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center max-w-sm">
        <RiBookOpenLine size={32} className="text-muted-foreground/50 mx-auto mb-3" />
        <h2 className="text-lg font-heading font-semibold text-foreground">Skills</h2>
        <p className="text-[13px] text-muted-foreground mt-2">
          Create skills that teach Loma how to complete your workflows.
        </p>
        <Link
          href={createUrl}
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-foreground hover:text-primary transition-colors mt-5"
        >
          <RiAddLine size={16} />
          Create skill
        </Link>
      </div>
    </div>
  );
}
