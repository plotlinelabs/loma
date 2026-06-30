"use client";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { RiInboxLine } from "@remixicon/react";

export function EmptyState({
  icon: Icon = RiInboxLine,
  title,
  description,
  action,
  onAction,
  className,
}: {
  icon?: React.ElementType;
  title: string;
  description?: string;
  action?: string;
  onAction?: () => void;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center py-8 text-center", className)}>
      <Icon size={32} className="text-muted-foreground/50 mb-3" />
      <p className="text-[13px] font-medium text-muted-foreground">{title}</p>
      {description && (
        <p className="text-xs text-muted-foreground/70 mt-1 max-w-xs">{description}</p>
      )}
      {action && onAction && (
        <Button variant="outline" size="sm" onClick={onAction} className="mt-3">
          {action}
        </Button>
      )}
    </div>
  );
}
