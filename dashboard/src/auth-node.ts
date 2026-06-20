import { readFile } from "node:fs/promises";
import path from "node:path";
import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import { MongoClient, type Document } from "mongodb";

type LocalAuth = {
  algorithm: "scrypt";
  salt: string;
  password_hash: string;
};

let mongoClientPromise: Promise<MongoClient> | null = null;

function getAuthProviders(): string[] {
  const configured = process.env.AUTH_PROVIDER || "local";
  return configured
    .split(",")
    .map((provider) => provider.trim().toLowerCase())
    .filter(Boolean);
}

function isProviderEnabled(name: string): boolean {
  const providers = getAuthProviders();
  return providers.includes("all") || providers.includes(name);
}

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

function getMongoClient(): Promise<MongoClient> {
  if (!process.env.OBSERVABILITY_MONGODB_URI) {
    throw new Error("OBSERVABILITY_MONGODB_URI is required for local dashboard auth");
  }
  mongoClientPromise ??= new MongoClient(process.env.OBSERVABILITY_MONGODB_URI).connect();
  return mongoClientPromise;
}

export async function getUsersCollection() {
  const client = await getMongoClient();
  const dbName = process.env.OBSERVABILITY_DB_NAME || "loma_observability";
  return client.db(dbName).collection("users");
}

export function normalizeEmail(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

export function hashPassword(password: string): LocalAuth {
  const salt = randomBytes(16).toString("base64url");
  const passwordHash = scryptSync(password, salt, 64).toString("base64url");
  return {
    algorithm: "scrypt",
    salt,
    password_hash: passwordHash,
  };
}

function verifyPassword(password: string, localAuth: LocalAuth): boolean {
  if (localAuth.algorithm !== "scrypt" || !localAuth.salt || !localAuth.password_hash) {
    return false;
  }
  const expected = Buffer.from(localAuth.password_hash, "base64url");
  const actual = scryptSync(password, localAuth.salt, 64);
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

async function authorizeLocal(credentials: Partial<Record<string, unknown>> | undefined) {
  const email = normalizeEmail(credentials?.email);
  const password = String(credentials?.password || "");
  const setupToken = String(credentials?.setupToken || "");

  if (!email || !password) {
    return null;
  }

  const users = await getUsersCollection();
  const existingUser = await users.findOne({ email });
  const localAuth = existingUser?.local_auth as LocalAuth | undefined;

  if (existingUser && localAuth) {
    if (!verifyPassword(password, localAuth)) {
      return null;
    }
    return {
      id: email,
      email,
      name: String(existingUser.name || email),
      image: String(existingUser.avatar || ""),
    };
  }

  const userCount = await users.countDocuments({});
  if (userCount > 0) {
    return null;
  }

  if (!process.env.LOMA_SETUP_TOKEN || setupToken !== process.env.LOMA_SETUP_TOKEN) {
    return null;
  }

  if (password.length < 8) {
    throw new Error("Password must be at least 8 characters");
  }

  const now = new Date();
  const userDoc: Document = {
    email,
    name: email.split("@")[0] || email,
    avatar: null,
    system_role: "admin",
    status: "active",
    tool_assignments: [],
    theme_preference: "system",
    claude_pool_enabled: true,
    local_auth: hashPassword(password),
    created_at: now,
    updated_at: now,
  };
  await users.insertOne(userDoc);

  return {
    id: email,
    email,
    name: String(userDoc.name),
    image: "",
  };
}

const providers = [];
if (isProviderEnabled("local")) {
  providers.push(
    Credentials({
      id: "credentials",
      name: "Email and password",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
        setupToken: { label: "Setup token", type: "text" },
      },
      authorize: authorizeLocal,
    }),
  );
}
if (isProviderEnabled("google")) {
  providers.push(Google);
}

export const { handlers } = NextAuth({
  providers,
  session: {
    strategy: "jwt",
  },
  callbacks: {
    async signIn({ user, account }) {
      if (account?.provider !== "google") {
        return true;
      }
      const email = (user.email ?? "").toLowerCase();
      const configuredDomains = await readAllowedEmailDomains();
      if (configuredDomains.length === 0) {
        return true;
      }
      return configuredDomains.some((domain) => email.endsWith(`@${domain}`));
    },
    jwt({ token, user }) {
      if (user?.email) {
        token.email = user.email;
        token.name = user.name;
        token.picture = user.image;
      }
      return token;
    },
    session({ session, token }) {
      if (session.user && token.email) {
        session.user.email = String(token.email);
        session.user.name = typeof token.name === "string" ? token.name : session.user.name;
        session.user.image = typeof token.picture === "string" ? token.picture : session.user.image;
      }
      return session;
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
