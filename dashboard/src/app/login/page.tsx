"use client";

import { signIn } from "next-auth/react";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useMemo, useState } from "react";
import CrosscutIcon from "../../components/CrosscutIcon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const error = searchParams.get("error");
  const authProviders = useMemo(
    () =>
      (process.env.NEXT_PUBLIC_AUTH_PROVIDER || "local")
        .split(",")
        .map((provider) => provider.trim().toLowerCase())
        .filter(Boolean),
    [],
  );
  const showLocal = authProviders.includes("all") || authProviders.includes("local");
  const showGoogle = authProviders.includes("all") || authProviders.includes("google");
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [setupToken, setSetupToken] = useState("");
  const [localError, setLocalError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const switchMode = (next: "signin" | "signup") => {
    setMode(next);
    setLocalError("");
  };

  const submitLocalLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLocalError("");
    setIsSubmitting(true);
    const result = await signIn("credentials", {
      email,
      password,
      setupToken,
      redirect: false,
      callbackUrl: "/",
    });
    setIsSubmitting(false);

    if (result?.ok) {
      router.push("/");
      router.refresh();
      return;
    }

    setLocalError("Unable to sign in. Check your email, password, and setup token.");
  };

  const submitSignup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLocalError("");
    setIsSubmitting(true);
    try {
      const res = await fetch("/api/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, name }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setIsSubmitting(false);
        setLocalError(data?.error || "Unable to create account.");
        return;
      }
      const result = await signIn("credentials", {
        email,
        password,
        redirect: false,
        callbackUrl: "/",
      });
      setIsSubmitting(false);
      if (result?.ok) {
        router.push("/");
        router.refresh();
        return;
      }
      setLocalError("Account created. Please sign in.");
      setMode("signin");
    } catch {
      setIsSubmitting(false);
      setLocalError("Unable to create account.");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <Card className="max-w-sm w-full shadow-sm">
        <CardContent className="p-3">
          <div className="mb-2 flex items-center justify-center gap-2">
            <CrosscutIcon size={36} />
            <span className="font-[family-name:var(--font-logo)] text-3xl font-black tracking-[1px] text-foreground">
              Loma
            </span>
          </div>
          <h1 className="text-xl font-heading font-semibold text-foreground mb-1 text-center">
            Loma Agent
          </h1>
          <p className="text-sm text-muted-foreground mb-2 text-center">
            Sign in to your company workspace.
          </p>

          {(error || localError) && (
            <Alert variant="destructive" className="mb-3">
              <AlertDescription>
                {localError ||
                (error === "AccessDenied"
                  ? "This email address is not allowed for this Loma workspace."
                  : "Something went wrong. Please try again.")}
              </AlertDescription>
            </Alert>
          )}

          {showLocal && mode === "signin" && (
            <form className="space-y-4" onSubmit={submitLocalLogin}>
              <div className="space-y-1.5">
                <Label htmlFor="signin-email">Email</Label>
                <Input
                  id="signin-email"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="signin-password">Password</Label>
                <Input
                  id="signin-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                />
              </div>
              <Button
                type="submit"
                disabled={isSubmitting}
                className="w-full"
                size="lg"
              >
                {isSubmitting ? "Signing in..." : "Sign in"}
              </Button>
              <div className="space-y-1.5">
                <Label htmlFor="setup-token" className="text-xs text-muted-foreground font-normal">
                  First admin setup token (leave blank otherwise)
                </Label>
                <Input
                  id="setup-token"
                  type="password"
                  value={setupToken}
                  onChange={(event) => setSetupToken(event.target.value)}
                  autoComplete="one-time-code"
                  className="text-xs"
                />
              </div>
              <p className="text-center text-sm text-muted-foreground">
                Need an account?{" "}
                <Button
                  type="button"
                  variant="link"
                  className="p-0 h-auto"
                  onClick={() => switchMode("signup")}
                >
                  Create one
                </Button>
              </p>
            </form>
          )}

          {showLocal && mode === "signup" && (
            <form className="space-y-4" onSubmit={submitSignup}>
              <div className="space-y-1.5">
                <Label htmlFor="signup-name">Name</Label>
                <Input
                  id="signup-name"
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  autoComplete="name"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="signup-email">Email</Label>
                <Input
                  id="signup-email"
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="signup-password">Password</Label>
                <Input
                  id="signup-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={8}
                />
                <span className="block text-xs text-muted-foreground">
                  At least 8 characters. New accounts require admin approval before access.
                </span>
              </div>
              <Button
                type="submit"
                disabled={isSubmitting}
                className="w-full"
                size="lg"
              >
                {isSubmitting ? "Creating account..." : "Create account"}
              </Button>
              <p className="text-center text-sm text-muted-foreground">
                Have an account?{" "}
                <Button
                  type="button"
                  variant="link"
                  className="p-0 h-auto"
                  onClick={() => switchMode("signin")}
                >
                  Sign in
                </Button>
              </p>
            </form>
          )}

          {showLocal && showGoogle && (
            <div className="my-5 flex items-center gap-2">
              <Separator className="flex-1" />
              <span className="text-xs text-muted-foreground">or</span>
              <Separator className="flex-1" />
            </div>
          )}

          {showGoogle && (
            <Button
              variant="outline"
              className="w-full"
              size="lg"
              onClick={() => signIn("google", { callbackUrl: "/" })}
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
            </Button>
          )}

          <p className="text-xs text-muted-foreground mt-2 text-center">
            Access is restricted to your configured workspace users.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center">
          <Skeleton className="h-[400px] w-[384px] rounded-2xl" />
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
