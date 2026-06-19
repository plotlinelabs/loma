"use client";

import { SessionProvider } from "next-auth/react";
import { UserProvider } from "../lib/UserContext";
import { ThemeProvider } from "../lib/ThemeContext";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <UserProvider>
        <ThemeProvider>{children}</ThemeProvider>
      </UserProvider>
    </SessionProvider>
  );
}
