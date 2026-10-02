"use client";

import { useState, useCallback, useEffect, Suspense } from "react";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import Sidebar from "./Sidebar";
import BottomNav from "./BottomNav";
import CrosscutIcon from "./CrosscutIcon";
import ViewportHeightSync from "./ViewportHeightSync";
import { useUser } from "../lib/UserContext";
import { useIsMobile } from "@/hooks/useIsMobile";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { MobileChromeProvider, MobileTopBar, useMobileChrome } from "@/components/mobile/MobileChrome";

export default function LayoutShell({ children }: { children: React.ReactNode }) {
  return (
    <MobileChromeProvider>
      <Shell>{children}</Shell>
    </MobileChromeProvider>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";
  const isMobile = useIsMobile();
  const { user, loading } = useUser();
  const { barHidden } = useMobileChrome();
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
  // The phone drawer is always expanded: the collapsed 56px rail hides every
  // section behind `!collapsed` and its expand button is desktop-only, so a
  // sidebar collapsed on desktop would open as an empty strip on a phone.
  const collapsed = sidebarCollapsed && !isMobile;
  // The drawer only exists below md. Deriving this (instead of trusting the raw
  // state) releases the drawer's body scroll lock when a phone rotates to
  // landscape or a window widens past the breakpoint with the drawer open.
  const drawerOpen = sidebarOpen && isMobile;
  // Also drop the raw state once the viewport reaches md, so narrowing the
  // window again (or rotating back to portrait) does not reopen the drawer.
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 768px)");
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setSidebarOpen(false);
    };
    desktop.addEventListener("change", onChange);
    return () => desktop.removeEventListener("change", onChange);
  }, []);

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
          isOpen={drawerOpen}
          onClose={closeSidebar}
          collapsed={collapsed}
          onToggleCollapse={toggleCollapse}
        />
      </Suspense>

      <ViewportHeightSync />
      <MobileTopBar onMenu={toggleSidebar} />

      <main className={cn(
        // --app-h tracks the visual viewport (ViewportHeightSync) so the
        // shell shrinks above the on-screen keyboard in browser tabs and the
        // installed PWA alike; dvh ignores the keyboard on iOS. Falls back to
        // dvh (not vh) so the iOS Safari URL bar never causes overflow.
        // overflow-x-clip keeps any wide child from panning the whole page.
        // Phones: clear the 48px top bar; the space is handed back to the page
        // while the bar is slid away on a downward scroll.
        "loma-dashboard ml-0 min-w-0 flex flex-col overflow-x-clip transition-all duration-200 motion-reduce:transition-none md:pt-0 bg-background",
        barHidden ? "pt-[env(safe-area-inset-top)]" : "pt-[calc(env(safe-area-inset-top)+3rem)]",
        "h-[var(--app-h,100dvh)]",
        sidebarCollapsed ? "md:ml-[56px]" : "md:ml-[220px]"
      )}>
        <div className={cn(
          "flex-1 w-full flex flex-col min-h-0",
          pathname.startsWith("/skills") ? "overflow-hidden" : "px-3 md:px-6 lg:px-8 pt-2 pb-4 md:py-6"
        )}>{children}</div>
        <BottomNav />
      </main>
    </>
  );
}
