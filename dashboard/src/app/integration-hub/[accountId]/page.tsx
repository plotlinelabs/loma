"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  fetchIntegrationAccount, formatIntegrationLabel, IntegrationAccount,
  IntegrationAccountInput, updateIntegrationAccount,
} from "@/lib/integration-hub-api";
import AccountForm from "@/components/integration-hub/AccountForm";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { RiArrowLeftLine } from "@remixicon/react";

export default function IntegrationAccountPage() {
  const { accountId } = useParams<{ accountId: string }>();
  const [account, setAccount] = useState<IntegrationAccount | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchIntegrationAccount(accountId)
      .then((data) => setAccount(data.account))
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load client"));
  }, [accountId]);

  async function save(input: IntegrationAccountInput) {
    const data = await updateIntegrationAccount(accountId, input);
    setAccount(data.account);
  }

  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>;
  if (!account) return <div className="space-y-3"><Skeleton className="h-8 w-64" /><Skeleton className="h-96 rounded-xl" /></div>;

  return (
    <div className="space-y-3 animate-fade-in-up">
      <Link href="/integration-hub" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><RiArrowLeftLine size={14} />Integration Hub</Link>
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg md:text-xl font-heading font-semibold">{account.name}</h1>
        <Badge variant="outline">{formatIntegrationLabel(account.stage)}</Badge>
        <Badge variant="outline">{formatIntegrationLabel(account.health)}</Badge>
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        <Card className="p-4">
          <h2 className="mb-3 font-heading text-base font-medium">Onboarding overview</h2>
          <AccountForm key={account.version} account={account} submitLabel="Save changes" onSubmit={save} />
        </Card>
        <div className="space-y-3">
          <Card className="p-4">
            <h2 className="font-medium">Current blocker</h2>
            <p className="mt-2 text-[13px] text-muted-foreground">{account.current_blocker || "No blocker recorded."}</p>
          </Card>
          <Card className="p-4">
            <h2 className="font-medium">Next action</h2>
            <p className="mt-2 text-[13px] text-muted-foreground">{account.next_action || "No next action recorded."}</p>
          </Card>
          <Card className="p-4">
            <h2 className="font-medium">Activity</h2>
            <p className="mt-2 text-[13px] text-muted-foreground">Last updated {new Date(account.updated_at).toLocaleString()}.</p>
          </Card>
        </div>
      </div>
    </div>
  );
}
