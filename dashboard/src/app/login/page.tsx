"use client";

import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import CrosscutIcon from "../../components/CrosscutIcon";

function LoginContent() {
  const searchParams = useSearchParams();
  const error = searchParams.get("error");

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-surface border border-gray-200 rounded-2xl p-8 max-w-sm w-full text-center shadow-sm">
        <div className="mb-4 flex items-center justify-center gap-3">
          <CrosscutIcon size={36} />
          <span className="font-[family-name:var(--font-logo)] text-3xl font-black tracking-[1px] text-gray-700">
            Loma
          </span>
        </div>
        <h1 className="text-xl font-semibold text-gray-900 mb-1">
          Loma Agent
        </h1>
        <p className="text-sm text-gray-500 mb-6">
          Sign in with your company Google account to continue.
        </p>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-6 text-sm text-red-700">
            {error === "AccessDenied"
              ? "This email address is not allowed for this Loma workspace."
              : "Something went wrong. Please try again."}
          </div>
        )}

        <button
          onClick={() => signIn("google", { callbackUrl: "/" })}
          className="w-full flex items-center justify-center gap-3 bg-surface hover:bg-gray-50 border border-gray-200 text-gray-700 font-medium px-4 py-2.5 rounded-xl transition-colors shadow-sm"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24">
            <path
              d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
              fill="#4285F4"
            />
            <path
              d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              fill="#34A853"
            />
            <path
              d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
              fill="#FBBC05"
            />
            <path
              d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
              fill="#EA4335"
            />
          </svg>
          Sign in with Google
        </button>

        <p className="text-xs text-gray-400 mt-4">
          Access is restricted to approved company domains
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center text-gray-400">
          Loading...
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
