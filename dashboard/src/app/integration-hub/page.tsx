"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  createIntegrationAccount, fetchIntegrationAccounts, formatIntegrationLabel,
  INTEGRATION_STAGES, IntegrationAccount, IntegrationAccountInput,
} from "@/lib/integration-hub-api";
import AccountForm from "@/components/integration-hub/AccountForm";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { EmptyState } from "@/components/EmptyState";
import { RiAddLine, RiBuildingLine } from "@remixicon/react";
import { cn } from "@/lib/utils";

const healthStyle: Record<string, string> = {
  on_track: "border-emerald-200 bg-emerald-50 text-emerald-700",
  needs_attention: "border-amber-200 bg-amber-50 text-amber-700",
  blocked: "border-red-200 bg-red-50 text-red-700",
  silent: "border-slate-200 bg-slate-50 text-slate-700",
  at_risk: "border-orange-200 bg-orange-50 text-orange-700",
  escalated: "border-purple-200 bg-purple-50 text-purple-700",
};

export default function IntegrationHubPage() {
  const [accounts, setAccounts] = useState<IntegrationAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchIntegrationAccounts({ search, stage: stage === "all" ? undefined : stage });
      setAccounts(data.accounts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load clients");
    } finally {
      setLoading(false);
    }
  }, [search, stage]);

  useEffect(() => {
    const timer = setTimeout(load, search ? 250 : 0);
    return () => clearTimeout(timer);
  }, [load, search]);

  async function create(input: IntegrationAccountInput) {
    await createIntegrationAccount(input);
    setDialogOpen(false);
    await load();
  }

  return (
    <div className="space-y-3 animate-fade-in-up">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold">Integration Hub</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">Manage client onboarding, ownership, blockers, and next actions.</p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild><Button size="sm"><RiAddLine />Add client</Button></DialogTrigger>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>Add onboarding client</DialogTitle>
              <DialogDescription>Create the operational record for a new client onboarding.</DialogDescription>
            </DialogHeader>
            <AccountForm submitLabel="Add client" onSubmit={create} />
          </DialogContent>
        </Dialog>
      </div>
      <div className="flex gap-2">
        <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search clients" className="max-w-sm" />
        <Select value={stage} onValueChange={setStage}>
          <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All stages</SelectItem>
            {INTEGRATION_STAGES.map((value) => <SelectItem key={value} value={value}>{formatIntegrationLabel(value)}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {loading ? (
        <div className="space-y-2">{[1, 2, 3].map((item) => <Skeleton key={item} className="h-24 rounded-xl" />)}</div>
      ) : accounts.length === 0 ? (
        <Card><EmptyState icon={RiBuildingLine} title="No onboarding clients yet" description="Add your first client to start tracking onboarding." action="Add client" onAction={() => setDialogOpen(true)} /></Card>
      ) : (
        <div className="space-y-2">
          {accounts.map((account) => (
            <Link key={account.account_id} href={`/integration-hub/${account.account_id}`}>
              <Card className="p-3 transition-colors hover:bg-muted/40">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="font-medium">{account.name}</h2>
                      <Badge variant="outline" className={cn("rounded-md", healthStyle[account.health])}>{formatIntegrationLabel(account.health)}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{formatIntegrationLabel(account.stage)} · {account.owner_email || "No owner"}</p>
                    {account.health_reason && <p className="mt-1 text-xs text-muted-foreground">{account.health_reason}</p>}
                  </div>
                  <div className="text-right text-xs">
                    <p className="text-muted-foreground">Target go-live</p>
                    <p>{account.target_go_live_at ? new Date(account.target_go_live_at).toLocaleDateString() : "Not set"}</p>
                  </div>
                </div>
                <div className="mt-3 grid gap-2 border-t pt-2 text-xs md:grid-cols-2">
                  <p><span className="text-muted-foreground">Blocker: </span>{account.current_blocker || "None"}</p>
                  <p><span className="text-muted-foreground">Next: </span>{account.next_action || "Not set"}</p>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
