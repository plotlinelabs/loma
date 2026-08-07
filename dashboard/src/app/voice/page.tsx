import { redirect } from "next/navigation";

export default function VoicePage() {
  redirect("/tasks?voice=1");
}
