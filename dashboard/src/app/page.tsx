"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { RiLoader4Line } from "@remixicon/react";
import { basePath } from "../lib/api";

/** The tasks board is the app's home now — send the root there. The chat
 * experience lives at /chat (the sidebar "Chat" item). router.replace in the
 * app router doesn't prepend basePath, so we add it explicitly. */
export default function RootRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace(`${basePath}/tasks`);
  }, [router]);
  return (
    <div className="flex flex-1 items-center justify-center">
      <RiLoader4Line className="h-5 w-5 animate-spin text-muted-foreground" />
    </div>
  );
}
