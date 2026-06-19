import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";

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

const providers = [];
if (isProviderEnabled("local")) {
  providers.push(
    Credentials({
      id: "credentials",
      credentials: {},
      authorize: async () => null,
    }),
  );
}
if (isProviderEnabled("google")) {
  providers.push(Google);
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers,
  session: {
    strategy: "jwt",
  },
  callbacks: {
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
