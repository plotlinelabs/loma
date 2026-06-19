"use client";

import { useSession, signOut } from "next-auth/react";

export default function Nav() {
  const { data: session, status } = useSession();

  return (
    <nav className="border-b border-gray-800 bg-gray-900/50 backdrop-blur-sm sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-14">
          <a href="/" className="flex items-center gap-2">
            <span className="text-lg font-semibold text-white">
              Loma
            </span>
            <span className="text-xs bg-blue-600 text-white px-2 py-0.5 rounded-full">
              Dashboard
            </span>
          </a>
          {status === "authenticated" && (
            <div className="flex items-center gap-3">
              {session?.user?.email && (
                <span className="text-sm text-gray-400">
                  {session.user.email}
                </span>
              )}
              <button
                onClick={() => signOut({ callbackUrl: "/login" })}
                className="text-sm text-gray-500 hover:text-gray-300 transition-colors"
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
