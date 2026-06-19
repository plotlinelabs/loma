import { readFile } from "node:fs/promises";
import path from "node:path";
import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

function parseAllowedEmailDomains(value: string | undefined): string[] {
  return (value || "")
    .split(",")
    .map((domain) => domain.trim().replace(/^@/, "").toLowerCase())
    .filter(Boolean);
}

async function readAllowedEmailDomains(): Promise<string[]> {
  try {
    const rootEnv = await readFile(path.join(process.cwd(), "..", ".env"), "utf8");
    const match = rootEnv.match(/^ALLOWED_EMAIL_DOMAINS=(.*)$/m);
    if (match) {
      return parseAllowedEmailDomains(match[1].replace(/^["']|["']$/g, ""));
    }
  } catch {
    // Fall back to the dashboard process env below.
  }
  return parseAllowedEmailDomains(process.env.ALLOWED_EMAIL_DOMAINS);
}

export const { handlers } = NextAuth({
  providers: [Google],
  callbacks: {
    async signIn({ user }) {
      const email = (user.email ?? "").toLowerCase();
      const configuredDomains = await readAllowedEmailDomains();
      return configuredDomains.some((domain) => email.endsWith(`@${domain}`));
    },
    authorized({ auth }) {
      return !!auth?.user;
    },
  },
  pages: {
    signIn: "/login",
    error: "/login",
  },
});
