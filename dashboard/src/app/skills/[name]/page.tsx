"use client";

import { useParams, redirect } from "next/navigation";

export default function SkillRedirect() {
  const params = useParams();
  const name = params.name as string;
  redirect(`/skills?skill=${encodeURIComponent(name)}`);
}
