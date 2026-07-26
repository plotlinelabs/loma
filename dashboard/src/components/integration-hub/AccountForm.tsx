"use client";

import { FormEvent, useState } from "react";
import {
  INTEGRATION_HEALTH, INTEGRATION_STAGES, IntegrationAccount,
  IntegrationAccountInput, formatIntegrationLabel,
} from "@/lib/integration-hub-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";

export default function AccountForm({
  account, submitLabel, onSubmit,
}: {
  account?: IntegrationAccount;
  submitLabel: string;
  onSubmit: (input: IntegrationAccountInput) => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState(account?.stage || "kickoff");
  const [health, setHealth] = useState(account?.health || "on_track");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    try {
      await onSubmit({
        name: String(data.get("name") || ""),
        owner_email: String(data.get("owner_email") || "") || null,
        stage,
        health,
        health_reason: String(data.get("health_reason") || "") || null,
        target_go_live_at: String(data.get("target_go_live_at") || "") || null,
        current_blocker: String(data.get("current_blocker") || "") || null,
        next_action: String(data.get("next_action") || "") || null,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save client");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <div className="space-y-1.5">
        <Label htmlFor="name">Client name</Label>
        <Input id="name" name="name" defaultValue={account?.name} required maxLength={200} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="owner_email">Owner</Label>
        <Input id="owner_email" name="owner_email" type="email" defaultValue={account?.owner_email || ""} placeholder="owner@plotline.so" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>Stage</Label>
          <Select value={stage} onValueChange={(value) => setStage(value as typeof stage)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>{INTEGRATION_STAGES.map((value) => <SelectItem key={value} value={value}>{formatIntegrationLabel(value)}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Health</Label>
          <Select value={health} onValueChange={(value) => setHealth(value as typeof health)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>{INTEGRATION_HEALTH.map((value) => <SelectItem key={value} value={value}>{formatIntegrationLabel(value)}</SelectItem>)}</SelectContent>
          </Select>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="health_reason">Health reason</Label>
        <Input id="health_reason" name="health_reason" defaultValue={account?.health_reason || ""} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="target_go_live_at">Target go-live</Label>
        <Input id="target_go_live_at" name="target_go_live_at" type="date" defaultValue={account?.target_go_live_at?.slice(0, 10) || ""} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="current_blocker">Current blocker</Label>
        <Textarea id="current_blocker" name="current_blocker" defaultValue={account?.current_blocker || ""} rows={2} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="next_action">Next action</Label>
        <Textarea id="next_action" name="next_action" defaultValue={account?.next_action || ""} rows={2} maxLength={500} />
      </div>
      <Button type="submit" disabled={saving} className="w-full">
        {saving ? "Saving..." : submitLabel}
      </Button>
    </form>
  );
}
