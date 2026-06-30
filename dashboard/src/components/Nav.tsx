"use client";

import { useSession, signOut } from "next-auth/react";
import { Button } from "@/components/ui/button";

export default function Nav() {
  const { data: session, status } = useSession();

  return (
    <nav className="border-b border-border bg-background/50 backdrop-blur-sm sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-4 lg:px-6">
        <div className="flex items-center justify-between h-14">
          <a href="/" className="flex items-center gap-2">
            <span className="text-lg font-semibold text-foreground">
              Loma
            </span>
            <span className="text-xs bg-blue-600 text-white px-2 py-0.5 rounded-full">
              Dashboard
            </span>
          </a>
          {status === "authenticated" && (
            <div className="flex items-center gap-3">
              {session?.user?.email && (
                <span className="text-sm text-muted-foreground">
                  {session.user.email}
                </span>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => signOut({ callbackUrl: "/login" })}
                className="text-muted-foreground hover:text-foreground"
              >
                Sign out
              </Button>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}
