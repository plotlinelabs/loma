"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { basePath } from "../../lib/api";

export default function CostRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace(`${basePath}/analytics`);
  }, [router]);

  return (
    <div className="flex items-center justify-center py-20">
      <div className="flex items-center gap-2 text-gray-400">
        <svg
          className="animate-spin w-4 h-4 text-brand-600"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          />
        </svg>
        Redirecting to Analytics...
      </div>
    </div>
  );
}
