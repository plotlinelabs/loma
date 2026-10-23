"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { RiAddLine, RiCheckLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { createTeam } from "../../../lib/governance-api";

export const TEAM_COLORS = [
  { color: "#2563EB", bg_color: "#DBEAFE" },
  { color: "#059669", bg_color: "#D1FAE5" },
  { color: "#D97706", bg_color: "#FEF3C7" },
  { color: "#DB2777", bg_color: "#FCE7F3" },
  { color: "#7C3AED", bg_color: "#EDE9FE" },
  { color: "#0891B2", bg_color: "#CFFAFE" },
  { color: "#DC2626", bg_color: "#FEE2E2" },
  { color: "#4B5563", bg_color: "#F3F4F6" },
];

export default function CreateTeamDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [colorIdx, setColorIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const team = await createTeam({ name: name.trim(), ...TEAM_COLORS[colorIdx] });
      setOpen(false);
      router.push(`/admin/teams/${team.team_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create team");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!busy) { setOpen(v); setError(null); } }}>
      <DialogTrigger asChild>
        <Button size="sm" className="bg-accent-200 text-accent-on hover:bg-accent-300 press-scale" data-testid="create-team-button">
          <RiAddLine size={16} />
          New team
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create a team</DialogTitle>
          <DialogDescription>Teams group people and agents. Skills and flows can then be organised by team.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="team-name">Name</Label>
            <Input
              id="team-name"
              autoFocus
              value={name}
              maxLength={60}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Customer Success"
              data-testid="create-team-name"
            />
          </div>
          <div className="space-y-1.5">
            <Label>Colour</Label>
            <div className="flex gap-2">
              {TEAM_COLORS.map((c, i) => (
                <button
                  key={c.color}
                  type="button"
                  onClick={() => setColorIdx(i)}
                  className={cn("w-7 h-7 rounded-md flex items-center justify-center border", i === colorIdx ? "border-foreground" : "border-transparent")}
                  style={{ backgroundColor: c.bg_color }}
                  aria-label={`Colour ${i + 1}`}
                >
                  {i === colorIdx ? <RiCheckLine size={14} style={{ color: c.color }} /> : <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: c.color }} />}
                </button>
              ))}
            </div>
          </div>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy || !name.trim()} data-testid="create-team-submit">
              {busy ? "Creating…" : "Create team"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
