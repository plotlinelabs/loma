"use client";

import { SessionProvider } from "next-auth/react";
import { UserProvider } from "../lib/UserContext";
import { ThemeProvider } from "../lib/ThemeContext";
import { TaskAttentionProvider } from "../lib/TaskAttentionContext";
import { TooltipProvider } from "@/components/ui/tooltip";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <UserProvider>
        <TaskAttentionProvider>
          <ThemeProvider>
            <TooltipProvider delayDuration={300}>
              {children}
            </TooltipProvider>
          </ThemeProvider>
        </TaskAttentionProvider>
      </UserProvider>
    </SessionProvider>
  );
}
