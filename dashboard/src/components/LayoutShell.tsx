"use client";

import { useState, useCallback, Suspense } from "react";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import Sidebar from "./Sidebar";
import CrosscutIcon from "./CrosscutIcon";
import { useUser } from "../lib/UserContext";

export default function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";
  const { user, loading } = useUser();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const toggleSidebar = useCallback(() => setSidebarOpen((prev) => !prev), []);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  if (isLogin) {
    return <>{children}</>;
  }

  // Users awaiting admin approval can't access the app yet.
  if (!loading && user?.status === "pending") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
        <div className="bg-surface border border-gray-200 rounded-2xl p-8 max-w-sm w-full text-center shadow-sm">
          <div className="mb-4 flex justify-center">
            <CrosscutIcon size={36} />
          </div>
          <h1 className="text-xl font-semibold text-gray-900 mb-2">Awaiting approval</h1>
          <p className="text-sm text-gray-500 mb-6">
            Your account is pending admin approval. You&apos;ll get access once an admin
            approves you.
          </p>
          <button
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="w-full rounded-xl bg-gray-900 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-gray-800"
          >
            Sign out
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      <Suspense>
        <Sidebar isOpen={sidebarOpen} onClose={closeSidebar} />
      </Suspense>

      {/* Mobile top bar */}
      <div className="md:hidden fixed top-0 left-0 right-0 h-14 bg-gray-100 border-b border-gray-200 flex items-center px-4 z-30">
        <button
          onClick={toggleSidebar}
          className="p-2 -ml-2 rounded-lg text-gray-600 hover:bg-gray-200/60 transition-colors press-scale"
          aria-label="Toggle menu"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </button>
        <div className="ml-3 flex items-center gap-2">
          <CrosscutIcon size={20} />
          <span className="font-[family-name:var(--font-logo)] text-sm font-black tracking-[0.5px] text-gray-800">Loma</span>
        </div>
      </div>

      <main className="ml-0 md:ml-[260px] h-screen pt-14 md:pt-0 flex flex-col">
        <div className={pathname.startsWith("/skills") ? "flex-1 w-full h-full overflow-hidden" : "px-4 md:px-6 lg:px-8 py-4 md:py-6 flex-1 w-full"}>{children}</div>
      </main>
    </>
  );
}
