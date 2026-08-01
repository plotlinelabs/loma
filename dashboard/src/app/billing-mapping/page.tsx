"use client";

import { useEffect, useState } from "react";
import {
  fetchBillingMapping,
  searchMnAccounts,
  linkOrg,
  unlinkOrg,
} from "../../lib/billing-api";
import type { OrgMapping, MnAccount, MappingStatus } from "../../lib/billing-api";
import { useUser } from "../../lib/UserContext";
import { cn } from "@/lib/utils";
import { statusColors } from "@/lib/status-colors";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { RiLoader4Line, RiSearchLine } from "@remixicon/react";
import { EmptyState } from "@/components/EmptyState";

const STATUS_LABELS: Record<MappingStatus, string> = {
  linked: "Linked",
  auto_matched: "Auto-matched",
  not_found: "Not found",
};

const STATUS_STYLES: Record<MappingStatus, string> = {
  linked: statusColors.active,
  auto_matched: statusColors.info,
  not_found: statusColors.error,
};

function LinkAccountDialog({
  org,
  onClose,
  onLinked,
}: {
  org: OrgMapping;
  onClose: () => void;
  onLinked: () => void;
}) {
  const [query, setQuery] = useState(org.org_name);
  const [results, setResults] = useState<MnAccount[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSearch() {
    setSearching(true);
    setError(null);
    try {
      setResults(await searchMnAccounts(query.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }

  async function handleLink(account: MnAccount) {
    setSaving(account.id);
    setError(null);
    try {
      await linkOrg(org.org_id, org.org_name, account);
      onLinked();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to link account");
      setSaving(null);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Link MonetizeNow account</DialogTitle>
          <DialogDescription>
            Search MonetizeNow accounts to link to <span className="font-medium">{org.org_name}</span>.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Search by name, ID, or custom ID"
          />
          <Button variant="outline" size="sm" onClick={handleSearch} disabled={searching || !query.trim()}>
            {searching ? <RiLoader4Line size={14} className="animate-spin" /> : <RiSearchLine size={14} />}
          </Button>
        </div>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <div className="max-h-60 overflow-y-auto space-y-1">
          {results.map((account) => (
            <div
              key={account.id}
              className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
            >
              <div className="min-w-0">
                <p className="text-[13px] font-medium truncate">{account.name}</p>
                <p className="text-xs text-muted-foreground truncate">
                  {account.id}
                  {account.custom_id ? ` · ${account.custom_id}` : ""}
                </p>
              </div>
              <Button size="sm" onClick={() => handleLink(account)} disabled={saving !== null}>
                {saving === account.id ? <RiLoader4Line size={14} className="animate-spin" /> : "Link"}
              </Button>
            </div>
          ))}
          {!searching && results.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-4">
              No results — search to find an account.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SkeletonRow() {
  return (
    <TableRow>
      <TableCell><Skeleton className="h-3 w-40" /></TableCell>
      <TableCell><Skeleton className="h-3 w-10" /></TableCell>
      <TableCell><Skeleton className="h-3 w-32" /></TableCell>
      <TableCell><Skeleton className="h-5 w-20 rounded" /></TableCell>
      <TableCell><Skeleton className="h-7 w-16 rounded" /></TableCell>
    </TableRow>
  );
}

export default function BillingMappingPage() {
  const { hasRole, loading: userLoading } = useUser();

  const [orgs, setOrgs] = useState<OrgMapping[]>([]);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [linkingOrg, setLinkingOrg] = useState<OrgMapping | null>(null);
  const [unlinking, setUnlinking] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const data = await fetchBillingMapping();
      setOrgs(data.organisations);
      setAccountsError(data.accounts_error);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load billing mapping");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleUnlink(org: OrgMapping) {
    setUnlinking(org.org_id);
    try {
      await unlinkOrg(org.org_id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to unlink");
    } finally {
      setUnlinking(null);
    }
  }

  if (!userLoading && !hasRole("maintainer")) {
    return (
      <Alert variant="destructive">
        <AlertDescription>Maintainer access required to view billing mapping.</AlertDescription>
      </Alert>
    );
  }

  const filtered = filter
    ? orgs.filter((o) => o.org_name.toLowerCase().includes(filter.toLowerCase()))
    : orgs;
  const notFoundCount = orgs.filter((o) => o.status === "not_found").length;

  return (
    <div className="space-y-2 animate-fade-in-up">
      {/* Header */}
      <div>
        <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">
          Billing Mapping
        </h1>
        <p className="text-[13px] text-muted-foreground mt-1">
          Link Plotline client organisations to their MonetizeNow billing accounts
          {!loading && notFoundCount > 0 && ` — ${notFoundCount} not linked`}
        </p>
      </div>

      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {accountsError && (
        <Alert variant="destructive">
          <AlertDescription>
            MonetizeNow accounts could not be fetched ({accountsError}) — auto-matching is unavailable, manual links still shown.
          </AlertDescription>
        </Alert>
      )}

      <Input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter clients..."
        className="max-w-xs"
      />

      <div className="rounded-lg border border-border overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Client</TableHead>
              <TableHead>Products</TableHead>
              <TableHead>MonetizeNow Account</TableHead>
              <TableHead>Status</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 6 }).map((_, i) => <SkeletonRow key={i} />)
            ) : filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5}>
                  <EmptyState title="No clients found" description="No organisations match the current filter." />
                </TableCell>
              </TableRow>
            ) : (
              filtered.map((org) => (
                <TableRow key={org.org_id}>
                  <TableCell>
                    <p className="text-[13px] font-medium">
                      {org.org_name}
                      {org.is_blocked && (
                        <Badge variant="outline" className={cn("rounded ml-2", statusColors.disabled)}>
                          blocked
                        </Badge>
                      )}
                    </p>
                    <p className="text-xs text-muted-foreground">{org.org_id}</p>
                  </TableCell>
                  <TableCell className="text-[13px]">{org.products_count}</TableCell>
                  <TableCell>
                    {org.account ? (
                      <>
                        <p className="text-[13px]">{org.account.name || org.account.id}</p>
                        <p className="text-xs text-muted-foreground">
                          {org.account.id}
                          {org.linked_by ? ` · linked by ${org.linked_by}` : ""}
                        </p>
                      </>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn("rounded", STATUS_STYLES[org.status])}>
                      {STATUS_LABELS[org.status]}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    {org.status === "linked" ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleUnlink(org)}
                        disabled={unlinking === org.org_id}
                      >
                        {unlinking === org.org_id ? (
                          <RiLoader4Line size={14} className="animate-spin" />
                        ) : (
                          "Unlink"
                        )}
                      </Button>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => setLinkingOrg(org)}>
                        {org.status === "auto_matched" ? "Confirm link" : "Link manually"}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {linkingOrg && (
        <LinkAccountDialog
          org={linkingOrg}
          onClose={() => setLinkingOrg(null)}
          onLinked={() => {
            setLinkingOrg(null);
            setLoading(true);
            load();
          }}
        />
      )}
    </div>
  );
}
