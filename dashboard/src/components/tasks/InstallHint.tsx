"use client";

import { useEffect, useState } from "react";
import { RiCloseLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { isPushConfigured } from "@/lib/push";
import { isIos, isStandalone } from "@/lib/pwa";

const DISMISSED_KEY = "loma-install-hint-dismissed";

/** One quiet line on /tasks nudging iOS Safari users to install the PWA —
 * home-screen install is what unlocks push notifications on iPhone. */
export function InstallHint() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!isIos() || isStandalone()) return;
    if (localStorage.getItem(DISMISSED_KEY)) return;
    // Only worth nudging when the server can actually send pushes.
    isPushConfigured().then((configured) => {
      if (configured) setVisible(true);
    });
  }, []);

  if (!visible) return null;

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>
        Get task alerts on this phone: <span className="text-foreground">Share → Add to Home Screen</span>
      </span>
      <Button
        variant="ghost" size="icon" className="h-6 w-6 shrink-0"
        aria-label="Dismiss"
        onClick={() => {
          localStorage.setItem(DISMISSED_KEY, "1");
          setVisible(false);
        }}
      >
        <RiCloseLine className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
