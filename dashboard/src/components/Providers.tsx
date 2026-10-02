"use client";

import { SessionProvider } from "next-auth/react";
import { UserProvider } from "../lib/UserContext";
import { ThemeProvider } from "../lib/ThemeContext";
import { TaskAttentionProvider } from "../lib/TaskAttentionContext";
import { NotificationsProvider } from "../lib/NotificationsContext";
import { BoardsProvider } from "../lib/BoardsContext";
import { TooltipProvider } from "@/components/ui/tooltip";

import { PetSettingsProvider } from "./PetCompanion";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <UserProvider>
        <TaskAttentionProvider>
          <NotificationsProvider>
          <BoardsProvider>
          <ThemeProvider>
            <TooltipProvider delayDuration={300}>
              <PetSettingsProvider>{children}</PetSettingsProvider>
            </TooltipProvider>
          </ThemeProvider>
          </BoardsProvider>
          </NotificationsProvider>
        </TaskAttentionProvider>
      </UserProvider>
    </SessionProvider>
  );
}
