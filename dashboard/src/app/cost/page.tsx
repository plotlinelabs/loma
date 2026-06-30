"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { basePath } from "../../lib/api";
import { RiLoader4Line } from "@remixicon/react";

export default function CostRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace(`${basePath}/analytics`);
  }, [router]);

  return (
    <div className="flex items-center justify-center py-20">
      <div className="flex items-center gap-2 text-muted-foreground">
        <RiLoader4Line size={16} className="animate-spin text-brand-600" />
        Redirecting to Analytics...
      </div>
    </div>
  );
}
