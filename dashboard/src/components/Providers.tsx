"use client";

import { SessionProvider } from "next-auth/react";
import { UserProvider } from "../lib/UserContext";
import { ThemeProvider } from "../lib/ThemeContext";
import { TooltipProvider } from "@/components/ui/tooltip";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <UserProvider>
        <ThemeProvider>
          <TooltipProvider delayDuration={300}>
            {children}
          </TooltipProvider>
        </ThemeProvider>
      </UserProvider>
    </SessionProvider>
  );
}
