"use client";

import { SessionProvider } from "next-auth/react";
import { UserProvider } from "../lib/UserContext";
import { ThemeProvider } from "../lib/ThemeContext";
import { TaskAttentionProvider } from "../lib/TaskAttentionContext";
import { NotificationsProvider } from "../lib/NotificationsContext";
import { TooltipProvider } from "@/components/ui/tooltip";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <UserProvider>
        <TaskAttentionProvider>
          <NotificationsProvider>
          <ThemeProvider>
            <TooltipProvider delayDuration={300}>
              {children}
            </TooltipProvider>
          </ThemeProvider>
          </NotificationsProvider>
        </TaskAttentionProvider>
      </UserProvider>
    </SessionProvider>
  );
}
