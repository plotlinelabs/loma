import { hashPassword, normalizeEmail, getUsersCollection } from "@/auth-node";

export const runtime = "nodejs";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(req: Request) {
  try {
    const body = await req.json().catch(() => ({}));
    const email = normalizeEmail(body?.email);
    const password = String(body?.password || "");
    const name = typeof body?.name === "string" ? body.name : "";

    if (!EMAIL_RE.test(email)) {
      return Response.json({ error: "Enter a valid email address." }, { status: 400 });
    }
    if (password.length < 8) {
      return Response.json(
        { error: "Password must be at least 8 characters." },
        { status: 400 },
      );
    }

    const users = await getUsersCollection();

    // The first account must be the admin, created via the setup token on the
    // sign-in screen — this avoids a public visitor grabbing admin / a no-admin deadlock.
    const count = await users.countDocuments({});
    if (count === 0) {
      return Response.json(
        { error: "The first admin must be created with the setup token on the sign-in screen." },
        { status: 403 },
      );
    }

    if (await users.findOne({ email })) {
      return Response.json(
        { error: "An account with this email already exists." },
        { status: 409 },
      );
    }

    const now = new Date();
    await users.insertOne({
      email,
      name: name.trim() || email.split("@")[0] || email,
      avatar: email[0].toUpperCase(),
      system_role: "chatter",
      status: "pending",
      tool_assignments: {},
      theme_preference: "system",
      claude_pool_enabled: true,
      local_auth: hashPassword(password),
      created_at: now,
      updated_at: now,
    });

    return Response.json({ ok: true }, { status: 201 });
  } catch {
    return Response.json({ error: "Signup failed" }, { status: 500 });
  }
}
