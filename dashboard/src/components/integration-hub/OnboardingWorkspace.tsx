"use client";

import { FormEvent, useState } from "react";
import {
  createIntegrationWorkItem,
  deleteIntegrationWorkItem,
  formatIntegrationLabel,
  IntegrationAccount,
  IntegrationWorkItemInput,
  IntegrationWorkItemType,
  updateIntegrationAccount,
  updateIntegrationWorkItem,
} from "@/lib/integration-hub-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { RiAddLine, RiDeleteBinLine } from "@remixicon/react";

const TYPES: IntegrationWorkItemType[] = ["milestone", "task", "risk", "blocker"];
const PLATFORMS = ["android", "ios", "react_native", "flutter", "web", "unity", "kmp"];
const ENVIRONMENTS = ["development", "staging", "production"];

const emptyItem: IntegrationWorkItemInput = {
  type: "task",
  title: "",
  description: null,
  status: "not_started",
  owner_email: null,
  due_at: null,
  severity: null,
  dependency: null,
  resolution: null,
  escalated: false,
};

export default function OnboardingWorkspace({
  account,
  onChange,
}: {
  account: IntegrationAccount;
  onChange: (account: IntegrationAccount) => void;
}) {
  const [item, setItem] = useState(emptyItem);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function savePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setSaving(true);
    setError(null);
    try {
      const data = await updateIntegrationAccount(account.account_id, {
        name: account.name,
        platforms: values.getAll("platforms") as string[],
        environments: values.getAll("environments") as string[],
        stakeholders: String(values.get("stakeholders") || "").split("\n").map((v) => v.trim()).filter(Boolean),
        go_live_criteria: String(values.get("go_live_criteria") || ""),
        completion_percentage: Number(values.get("completion_percentage")),
      });
      onChange(data.account);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save onboarding plan");
    } finally {
      setSaving(false);
    }
  }

  async function addItem(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const data = await createIntegrationWorkItem(account.account_id, item);
      onChange(data.account);
      setItem(emptyItem);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add item");
    } finally {
      setSaving(false);
    }
  }

  async function setStatus(itemId: string, status: IntegrationWorkItemInput["status"]) {
    const data = await updateIntegrationWorkItem(account.account_id, itemId, { status });
    onChange(data.account);
  }

  async function removeItem(itemId: string) {
    const data = await deleteIntegrationWorkItem(account.account_id, itemId);
    onChange(data.account);
  }

  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Card className="p-4">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="font-heading text-base font-medium">Onboarding plan</h2>
            <p className="text-xs text-muted-foreground">Define the implementation scope and go-live criteria.</p>
          </div>
          <Badge variant="outline">{account.completion_percentage || 0}% complete</Badge>
        </div>
        <form onSubmit={savePlan} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <fieldset>
              <Label>SDK platforms</Label>
              <div className="mt-2 flex flex-wrap gap-3">
                {PLATFORMS.map((value) => <label key={value} className="flex items-center gap-1.5 text-xs"><input type="checkbox" name="platforms" value={value} defaultChecked={(account.platforms || []).includes(value)} />{formatIntegrationLabel(value)}</label>)}
              </div>
            </fieldset>
            <fieldset>
              <Label>Environments</Label>
              <div className="mt-2 flex flex-wrap gap-3">
                {ENVIRONMENTS.map((value) => <label key={value} className="flex items-center gap-1.5 text-xs"><input type="checkbox" name="environments" value={value} defaultChecked={(account.environments || []).includes(value)} />{formatIntegrationLabel(value)}</label>)}
              </div>
            </fieldset>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div><Label htmlFor="stakeholders">Stakeholders</Label><Textarea id="stakeholders" name="stakeholders" rows={3} defaultValue={(account.stakeholders || []).join("\n")} placeholder="One name or email per line" /></div>
            <div><Label htmlFor="go_live_criteria">Go-live criteria</Label><Textarea id="go_live_criteria" name="go_live_criteria" rows={3} defaultValue={account.go_live_criteria || ""} /></div>
          </div>
          <div className="flex items-end gap-3">
            <div className="w-40"><Label htmlFor="completion_percentage">Completion %</Label><Input id="completion_percentage" name="completion_percentage" type="number" min={0} max={100} defaultValue={account.completion_percentage || 0} /></div>
            <Button type="submit" size="sm" disabled={saving}>Save plan</Button>
          </div>
        </form>
      </Card>

      <Card className="p-4">
        <h2 className="font-heading text-base font-medium">Milestones, tasks, risks and blockers</h2>
        <div className="mt-4 space-y-2">
          {TYPES.map((type) => {
            const items = (account.work_items || []).filter((entry) => entry.type === type);
            return (
              <section key={type} className="rounded-lg border p-3">
                <div className="mb-2 flex items-center justify-between"><h3 className="text-sm font-medium">{formatIntegrationLabel(type)}s</h3><Badge variant="secondary">{items.length}</Badge></div>
                {items.length === 0 ? <p className="text-xs text-muted-foreground">No {type}s added.</p> : (
                  <div className="space-y-2">
                    {items.map((entry) => (
                      <div key={entry.item_id} className="flex flex-wrap items-center gap-2 rounded-md bg-muted/40 p-2">
                        <div className="min-w-48 flex-1"><p className="text-sm font-medium">{entry.title}</p><p className="text-xs text-muted-foreground">{entry.owner_email || "Unassigned"}{entry.due_at ? ` · Due ${new Date(entry.due_at).toLocaleDateString()}` : ""}{entry.severity ? ` · ${formatIntegrationLabel(entry.severity)}` : ""}</p></div>
                        <select className="h-8 rounded-md border bg-background px-2 text-xs" value={entry.status} onChange={(event) => setStatus(entry.item_id, event.target.value as IntegrationWorkItemInput["status"])}>
                          {["not_started", "in_progress", "blocked", "completed"].map((status) => <option key={status} value={status}>{formatIntegrationLabel(status)}</option>)}
                        </select>
                        <Button variant="ghost" size="icon-sm" onClick={() => removeItem(entry.item_id)} aria-label={`Delete ${entry.title}`}><RiDeleteBinLine /></Button>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>
        <form onSubmit={addItem} className="mt-4 grid gap-3 rounded-lg border p-3 md:grid-cols-2 lg:grid-cols-4">
          <div><Label>Type</Label><select className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={item.type} onChange={(event) => setItem({ ...item, type: event.target.value as IntegrationWorkItemType })}>{TYPES.map((type) => <option key={type} value={type}>{formatIntegrationLabel(type)}</option>)}</select></div>
          <div><Label>Title</Label><Input required value={item.title} onChange={(event) => setItem({ ...item, title: event.target.value })} /></div>
          <div><Label>Owner</Label><Input type="email" value={item.owner_email || ""} onChange={(event) => setItem({ ...item, owner_email: event.target.value || null })} /></div>
          <div><Label>Due date</Label><Input type="date" value={item.due_at || ""} onChange={(event) => setItem({ ...item, due_at: event.target.value || null })} /></div>
          {(item.type === "risk" || item.type === "blocker") && <div><Label>Severity</Label><select className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={item.severity || ""} onChange={(event) => setItem({ ...item, severity: (event.target.value || null) as IntegrationWorkItemInput["severity"] })}><option value="">Not set</option>{["low", "medium", "high", "critical"].map((value) => <option key={value} value={value}>{formatIntegrationLabel(value)}</option>)}</select></div>}
          <div className="flex items-end"><Button type="submit" size="sm" disabled={saving || !item.title.trim()}><RiAddLine />Add item</Button></div>
        </form>
      </Card>
    </div>
  );
}
