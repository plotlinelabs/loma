"use client";

import { Fragment, useEffect, useState } from "react";
import { RiArrowDownSLine, RiExternalLinkLine, RiLoader4Line, RiRefreshLine } from "@remixicon/react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchActiveContracts, fetchBillingMappings, setBillingAccount, setProductContract, type BillingContract, type BillingOrganization, type BillingPagination, type BillingStatus } from "@/lib/billing-mapping-api";

const labels: Record<BillingStatus, string> = { correctly_linked: "Correct", contract_missing: "Missing contract", invalid_contract: "Invalid contract", inactive_contract: "Inactive contract", account_mismatch: "Account mismatch", account_not_linked: "Account not linked" };
const colors: Record<BillingStatus, string> = { correctly_linked: "bg-emerald-50 text-emerald-700", contract_missing: "bg-amber-50 text-amber-700", invalid_contract: "bg-red-50 text-red-700", inactive_contract: "bg-red-50 text-red-700", account_mismatch: "bg-orange-50 text-orange-700", account_not_linked: "bg-slate-100 text-slate-700" };

export default function BillingMappingPage() {
  const [organizations, setOrganizations] = useState<BillingOrganization[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [accountValues, setAccountValues] = useState<Record<string,string>>({});
  const [contracts, setContracts] = useState<Record<string,BillingContract[]>>({});
  const [pagination, setPagination] = useState<BillingPagination>({ page: 1, pageSize: 25, total: 0, hasNext: false });

  async function load(page = pagination.page, status = filter) {
    setLoading(true); setError(null);
    try { const result = await fetchBillingMappings(page, pagination.pageSize, status); const rows = result.organizations; setOrganizations(rows); setPagination(result.pagination); setAccountValues(Object.fromEntries(rows.map(o => [o.id, o.monetizeNowAccountId || ""]))); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to load billing mappings"); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(1); }, []);

  async function saveAccount(org: BillingOrganization) {
    setSaving(org.id); setError(null);
    try {
      await setBillingAccount(org.id, accountValues[org.id] || "");
      await load();
      const active = await fetchActiveContracts(org.id);
      setContracts(v => ({...v, [org.id]: active}));
    }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to save account"); }
    finally { setSaving(null); }
  }
  async function openOrg(org: BillingOrganization) {
    const next = expanded === org.id ? null : org.id; setExpanded(next);
    if (next && org.monetizeNowAccountId && !contracts[org.id]) {
      try { const active = await fetchActiveContracts(org.id); setContracts(v => ({...v, [org.id]: active})); }
      catch (e) { setError(e instanceof Error ? e.message : "Failed to load contracts"); }
    }
  }
  async function saveContract(orgId: string, productId: string, contractId: string) {
    setSaving(productId); setError(null);
    try { await setProductContract(productId, contractId); await load(); setExpanded(orgId); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to link contract"); }
    finally { setSaving(null); }
  }
  return <div className="space-y-4 animate-fade-in-up">
    <div className="flex items-start justify-between gap-3"><div><h1 className="text-lg md:text-xl font-heading font-semibold">Billing Mapping</h1><p className="text-[13px] text-muted-foreground mt-1">Validate Plotline organizations and products against active MonetizeNow contracts.</p></div><Button variant="outline" size="sm" onClick={() => load()} disabled={loading}><RiRefreshLine size={15}/>Refresh</Button></div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex items-center justify-between gap-2"><Select value={filter} onValueChange={value => { setFilter(value); void load(1, value); }}><SelectTrigger className="w-[210px]"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">All organizations</SelectItem>{Object.entries(labels).map(([value,label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select><span className="text-xs text-muted-foreground">{pagination.total} organizations</span></div>
    <Card className="overflow-hidden"><Table><TableHeader><TableRow><TableHead>Organization</TableHead><TableHead>MonetizeNow Account ID</TableHead><TableHead>Products</TableHead><TableHead>Health</TableHead><TableHead className="w-10"/></TableRow></TableHeader><TableBody>
      {loading ? <TableRow><TableCell colSpan={5} className="h-28 text-center"><RiLoader4Line className="inline animate-spin"/> Loading mappings...</TableCell></TableRow> : organizations.map(org => <Fragment key={org.id}>
        <TableRow key={org.id} className="cursor-pointer" onClick={() => openOrg(org)}><TableCell className="font-medium">{org.name}{org.isBlocked && <Badge variant="outline" className="ml-2">Blocked</Badge>}</TableCell><TableCell onClick={e => e.stopPropagation()}><div className="flex gap-2"><Input className="h-8 max-w-[300px] font-mono text-xs" value={accountValues[org.id] || ""} placeholder="acct_..." onChange={e => setAccountValues(v => ({...v,[org.id]:e.target.value}))}/><Button size="sm" variant="outline" disabled={saving === org.id || !accountValues[org.id]} onClick={() => saveAccount(org)}>{saving === org.id ? "Saving..." : "Link"}</Button></div></TableCell><TableCell>{org.products.length}</TableCell><TableCell>{org.products.every(p => p.status === "correctly_linked") && org.products.length ? <Badge className={colors.correctly_linked}>Ready</Badge> : <Badge className="bg-amber-50 text-amber-700">Needs action</Badge>}</TableCell><TableCell><RiArrowDownSLine className={expanded === org.id ? "rotate-180" : ""}/></TableCell></TableRow>
        {expanded === org.id && <TableRow key={`${org.id}-details`}><TableCell colSpan={5} className="bg-muted/30 p-4"><Table><TableHeader><TableRow><TableHead>Product</TableHead><TableHead>Status</TableHead><TableHead>Current contract</TableHead><TableHead>Active contract</TableHead></TableRow></TableHeader><TableBody>{org.products.map(product => { const activeContracts = contracts[org.id] || []; const currentIsActive = Boolean(product.billingId && activeContracts.some(c => c.id === product.billingId)); return <TableRow key={product.id}><TableCell>{product.name}<div className="text-xs text-muted-foreground font-mono">{product.id}</div></TableCell><TableCell><Badge className={colors[product.status]}>{labels[product.status]}</Badge></TableCell><TableCell className="font-mono text-xs">{product.billingId || "Not linked"}{product.contract?.url && <a href={product.contract.url} target="_blank" rel="noreferrer" className="ml-2 text-brand-600"><RiExternalLinkLine size={14} className="inline"/></a>}</TableCell><TableCell>{org.monetizeNowAccountId ? <Select disabled={saving === product.id} value={product.billingId || undefined} onValueChange={value => saveContract(org.id, product.id, value)}><SelectTrigger className="w-[280px]"><SelectValue placeholder="Select active contract"/></SelectTrigger><SelectContent>{product.billingId && !currentIsActive && <SelectItem value={product.billingId} disabled>Current: {product.billingId} (not active)</SelectItem>}{activeContracts.map(c => <SelectItem key={c.id} value={c.id}>{c.name} ({c.id})</SelectItem>)}</SelectContent></Select> : <span className="text-xs text-muted-foreground">Link the account first</span>}</TableCell></TableRow>; })}</TableBody></Table></TableCell></TableRow>}
      </Fragment>)}
      {!loading && !organizations.length && <TableRow><TableCell colSpan={5} className="h-24 text-center text-muted-foreground">No organizations match this filter.</TableCell></TableRow>}
    </TableBody></Table></Card>
    <div className="flex items-center justify-end gap-2"><Button variant="outline" size="sm" disabled={loading || pagination.page <= 1} onClick={() => load(pagination.page - 1)}>Previous</Button><span className="text-xs text-muted-foreground">Page {pagination.page}</span><Button variant="outline" size="sm" disabled={loading || !pagination.hasNext} onClick={() => load(pagination.page + 1)}>Next</Button></div>
  </div>;
}
