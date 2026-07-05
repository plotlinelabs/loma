"use client";

import { useState, useCallback, useEffect, Suspense } from "react";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import Sidebar from "./Sidebar";
import CrosscutIcon from "./CrosscutIcon";
import ViewportHeightSync from "./ViewportHeightSync";
import { useUser } from "../lib/UserContext";
import { useStandalone } from "@/hooks/useStandalone";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { RiMenuLine } from "@remixicon/react";

export default function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";
  const standalone = useStandalone();
  const { user, loading } = useUser();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem("sidebar-collapsed") === "true";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("sidebar-collapsed", String(sidebarCollapsed));
    } catch {}
  }, [sidebarCollapsed]);

  const toggleSidebar = useCallback(() => setSidebarOpen((prev) => !prev), []);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const toggleCollapse = useCallback(() => setSidebarCollapsed((prev) => !prev), []);

  if (isLogin) {
    return <>{children}</>;
  }

  // Users awaiting admin approval can't access the app yet.
  if (!loading && user?.status === "pending") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-muted p-4">
        <div className="bg-background border border-border rounded-2xl p-5 max-w-sm w-full text-center shadow-sm">
          <div className="mb-4 flex justify-center">
            <CrosscutIcon size={36} />
          </div>
          <h1 className="text-xl font-heading font-semibold text-foreground mb-2">Awaiting approval</h1>
          <p className="text-[13px] text-muted-foreground mb-3">
            Your account is pending admin approval. You&apos;ll get access once an admin
            approves you.
          </p>
          <Button
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="w-full rounded-xl"
            size="lg"
          >
            Sign out
          </Button>
        </div>
      </div>
    );
  }

  return (
    <>
      <Suspense>
        <Sidebar
          isOpen={sidebarOpen}
          onClose={closeSidebar}
          collapsed={sidebarCollapsed}
          onToggleCollapse={toggleCollapse}
        />
      </Suspense>

      {/* Mobile: no dedicated top bar — a floating menu button keeps every
          vertical pixel for content. Page headers make room for it via
          .pwa-header-offset (globals.css). */}
      {standalone && <ViewportHeightSync />}
      <Button
        variant="ghost"
        size="icon"
        onClick={toggleSidebar}
        aria-label="Toggle menu"
        className="md:hidden fixed left-3 top-[max(0.5rem,env(safe-area-inset-top))] z-30 h-9 w-9 rounded-full border border-border bg-background/80 backdrop-blur text-muted-foreground press-scale"
      >
        <RiMenuLine size={18} />
      </Button>

      <main className={cn(
        // dvh (not vh) so the iOS Safari URL bar doesn't cause overflow; in
        // the installed PWA, --app-h tracks the visual viewport so the layout
        // shrinks above the on-screen keyboard (dvh ignores it on iOS).
        "ml-0 flex flex-col transition-all duration-200 pt-[env(safe-area-inset-top)] md:pt-0",
        standalone ? "h-[var(--app-h,100dvh)]" : "h-dvh",
        sidebarCollapsed ? "md:ml-[56px]" : "md:ml-[220px]"
      )}>
        <div className={cn(
          "flex-1 w-full flex flex-col min-h-0",
          pathname.startsWith("/skills") ? "overflow-hidden" : "px-3 md:px-3 lg:px-4 py-3"
        )}>{children}</div>
      </main>
    </>
  );
}
