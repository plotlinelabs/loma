"use client";

import { useState, useCallback, useEffect, Suspense } from "react";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import Sidebar from "./Sidebar";
import CrosscutIcon from "./CrosscutIcon";
import { useUser } from "../lib/UserContext";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { RiMenuLine } from "@remixicon/react";

export default function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";
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

      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 h-14 bg-muted border-b border-border flex items-center px-4 z-30">
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          className="-ml-2 text-muted-foreground hover:text-foreground press-scale"
          aria-label="Toggle menu"
        >
          <RiMenuLine size={20} />
        </Button>
        <div className="ml-3 flex items-center gap-2">
          <CrosscutIcon size={20} />
          <span className="font-[family-name:var(--font-logo)] text-[13px] font-black tracking-[0.5px] text-foreground">Loma</span>
        </div>
      </div>

      <main className={cn(
        "ml-0 h-screen pt-14 md:pt-0 flex flex-col transition-all duration-200",
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
