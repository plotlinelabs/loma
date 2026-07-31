"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import {
  RiAlertLine,
  RiArrowDownSLine,
  RiExternalLinkLine,
  RiLoader4Line,
  RiRefreshLine,
} from "@remixicon/react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  fetchActiveContracts,
  fetchBillingMappings,
  setBillingAccount,
  setProductContract,
  type BillingContract,
  type BillingOrganization,
  type BillingPagination,
  type BillingProduct,
  type BillingStatus,
} from "@/lib/billing-mapping-api";

const STATUS_LABELS: Record<BillingStatus, string> = {
  correctly_linked: "Correct",
  contract_missing: "No contract",
  invalid_contract: "Invalid contract",
  inactive_contract: "Inactive contract",
  account_mismatch: "Account mismatch",
  unknown: "Lookup failed",
};

const STATUS_COLORS: Record<BillingStatus, string> = {
  correctly_linked: "bg-emerald-50 text-emerald-700",
  contract_missing: "bg-amber-50 text-amber-700",
  invalid_contract: "bg-red-50 text-red-700",
  inactive_contract: "bg-orange-50 text-orange-700",
  account_mismatch: "bg-red-50 text-red-700",
  unknown: "bg-slate-100 text-slate-600",
};

const STATUS_ORDER: BillingStatus[] = [
  "correctly_linked",
  "contract_missing",
  "invalid_contract",
  "inactive_contract",
  "account_mismatch",
  "unknown",
];

function StatusBadge({ status }: { status: BillingStatus }) {
  return (
    <Badge variant="outline" className={STATUS_COLORS[status]}>
      {STATUS_LABELS[status]}
    </Badge>
  );
}

function OrganizationHealth({ org }: { org: BillingOrganization }) {
  const problems = STATUS_ORDER.filter(
    (status) => status !== "correctly_linked" && org.summary[status] > 0,
  );
  if (!org.products.length) {
    return <span className="text-xs text-muted-foreground">No products</span>;
  }
  if (!problems.length) {
    return <StatusBadge status="correctly_linked" />;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {problems.map((status) => (
        <Badge key={status} variant="outline" className={STATUS_COLORS[status]}>
          {org.summary[status]} {STATUS_LABELS[status].toLowerCase()}
        </Badge>
      ))}
    </div>
  );
}

function AccountCell({ org }: { org: BillingOrganization }) {
  if (!org.accountId) {
    return <span className="text-xs text-muted-foreground">Not resolved</span>;
  }
  return (
    <div className="min-w-0">
      <div className="truncate text-[13px] font-medium">
        {org.accountName || "Unnamed account"}
      </div>
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[11px] text-muted-foreground">{org.accountId}</span>
        {org.accountSource === "override" && (
          <Badge variant="outline" className="px-1 py-0 text-[10px]">
            override
          </Badge>
        )}
      </div>
    </div>
  );
}

function ProductRow({
  product,
  contracts,
  saving,
  onLink,
}: {
  product: BillingProduct;
  contracts: BillingContract[];
  saving: string | null;
  onLink: (productId: string, contractId: string) => void;
}) {
  return (
    <TableRow>
      <TableCell className="font-medium">
        {product.name}
        {product.sharedWithOrganizations.length > 0 && (
          <div className="mt-1 flex items-center gap-1 text-[11px] text-red-600">
            <RiAlertLine size={12} />
            Contract also used by {product.sharedWithOrganizations.join(", ")}
          </div>
        )}
      </TableCell>
      <TableCell>
        <StatusBadge status={product.status} />
      </TableCell>
      <TableCell>
        {product.contract ? (
          <div className="min-w-0">
            <div className="truncate text-[13px]">{product.contract.name}</div>
            <div className="font-mono text-[11px] text-muted-foreground">
              {product.contract.accountName || product.contract.accountId}
            </div>
          </div>
        ) : (
          <span className="font-mono text-[11px] text-muted-foreground">
            {product.billingId || "—"}
          </span>
        )}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex items-center justify-end gap-2">
          {product.contract?.url && (
            <a
              href={product.contract.url}
              target="_blank"
              rel="noreferrer"
              className="text-muted-foreground hover:text-foreground"
              title="Open contract"
            >
              <RiExternalLinkLine size={14} />
            </a>
          )}
          <Select
            disabled={!contracts.length || saving === product.id}
            onValueChange={(contractId) => onLink(product.id, contractId)}
          >
            <SelectTrigger className="w-[220px]">
              <SelectValue placeholder={contracts.length ? "Link contract" : "No contracts"} />
            </SelectTrigger>
            <SelectContent>
              {contracts.map((contract) => (
                <SelectItem key={contract.id} value={contract.id}>
                  {contract.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </TableCell>
    </TableRow>
  );
}

export default function BillingMappingPage() {
  const [organizations, setOrganizations] = useState<BillingOrganization[]>([]);
  const [totals, setTotals] = useState<Record<BillingStatus, number> | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [upstreamFailures, setUpstreamFailures] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [accountInputs, setAccountInputs] = useState<Record<string, string>>({});
  const [contracts, setContracts] = useState<Record<string, BillingContract[]>>({});
  const [pagination, setPagination] = useState<BillingPagination>({
    page: 1,
    pageSize: 25,
    total: 0,
    hasNext: false,
  });

  const load = useCallback(
    async (page = 1, status = filter, query = search, refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchBillingMappings(
          page,
          pagination.pageSize,
          status,
          query,
          refresh,
        );
        setOrganizations(result.organizations);
        setPagination(result.pagination);
        setTotals(result.totals);
        setGeneratedAt(result.generatedAt);
        setUpstreamFailures(result.upstreamFailures);
        setAccountInputs(
          Object.fromEntries(
            result.organizations.map((org) => [
              org.id,
              org.accountSource === "override" ? org.accountId || "" : "",
            ]),
          ),
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load billing mappings");
      } finally {
        setLoading(false);
      }
    },
    [filter, search, pagination.pageSize],
  );

  useEffect(() => {
    void load(1);
    // Initial load only; subsequent loads are driven by explicit user actions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadContracts(orgId: string) {
    try {
      const active = await fetchActiveContracts(orgId);
      setContracts((current) => ({ ...current, [orgId]: active }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load contracts");
    }
  }

  async function toggleOrg(org: BillingOrganization) {
    const next = expanded === org.id ? null : org.id;
    setExpanded(next);
    if (next && org.accountId && !contracts[org.id]) {
      await loadContracts(org.id);
    }
  }

  async function saveAccount(org: BillingOrganization) {
    setSaving(org.id);
    setError(null);
    try {
      await setBillingAccount(org.id, accountInputs[org.id] || "");
      await load(pagination.page);
      await loadContracts(org.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save account");
    } finally {
      setSaving(null);
    }
  }

  async function linkContract(orgId: string, productId: string, contractId: string) {
    setSaving(productId);
    setError(null);
    try {
      await setProductContract(productId, contractId);
      await load(pagination.page);
      setExpanded(orgId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to link contract");
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="space-y-4 animate-fade-in-up">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold">Billing Mapping</h1>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Reconcile organizations and their products against billing accounts and contracts.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={loading}
          onClick={() => load(pagination.page, filter, search, true)}
        >
          <RiRefreshLine size={14} className={loading ? "animate-spin" : ""} />
          Refresh
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {upstreamFailures > 0 && (
        <Alert>
          <AlertDescription>
            {upstreamFailures} contract lookup(s) failed upstream and are shown as
            &quot;lookup failed&quot; rather than as broken links. Refresh to retry.
          </AlertDescription>
        </Alert>
      )}

      {totals && (
        <div className="flex flex-wrap gap-2">
          {STATUS_ORDER.filter((status) => totals[status] > 0).map((status) => (
            <Badge key={status} variant="outline" className={STATUS_COLORS[status]}>
              {totals[status]} {STATUS_LABELS[status].toLowerCase()}
            </Badge>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Select
            value={filter}
            onValueChange={(value) => {
              setFilter(value);
              void load(1, value, search);
            }}
          >
            <SelectTrigger className="w-[210px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All organizations</SelectItem>
              {STATUS_ORDER.map((status) => (
                <SelectItem key={status} value={status}>
                  {STATUS_LABELS[status]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            placeholder="Search organization or account"
            className="w-[260px]"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void load(1, filter, search);
            }}
          />
        </div>
        {generatedAt && (
          <span className="text-[11px] text-muted-foreground">
            As of {new Date(generatedAt).toLocaleString()}
          </span>
        )}
      </div>

      <Card className="overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Organization</TableHead>
              <TableHead>Billing account</TableHead>
              <TableHead>Products</TableHead>
              <TableHead>Health</TableHead>
              <TableHead className="text-right">Override account</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={5} className="h-28 text-center">
                  <RiLoader4Line className="inline animate-spin" /> Loading mappings...
                </TableCell>
              </TableRow>
            ) : (
              organizations.map((org) => (
                <Fragment key={org.id}>
                  <TableRow className="cursor-pointer" onClick={() => toggleOrg(org)}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-1.5">
                        <RiArrowDownSLine
                          size={14}
                          className={expanded === org.id ? "rotate-180" : ""}
                        />
                        {org.name}
                        {org.dashboardDisabled && (
                          <Badge variant="outline" className="ml-1">
                            Disabled
                          </Badge>
                        )}
                      </div>
                      {org.danglingProductRefs > 0 && (
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {org.danglingProductRefs} deleted product reference(s)
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <AccountCell org={org} />
                    </TableCell>
                    <TableCell>{org.products.length}</TableCell>
                    <TableCell>
                      <OrganizationHealth org={org} />
                    </TableCell>
                    <TableCell className="text-right" onClick={(event) => event.stopPropagation()}>
                      <div className="flex items-center justify-end gap-2">
                        <Input
                          placeholder="Account ID"
                          className="w-[190px]"
                          value={accountInputs[org.id] ?? ""}
                          onChange={(event) =>
                            setAccountInputs((current) => ({
                              ...current,
                              [org.id]: event.target.value,
                            }))
                          }
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={saving === org.id}
                          onClick={() => saveAccount(org)}
                        >
                          Save
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                  {expanded === org.id && (
                    <TableRow>
                      <TableCell colSpan={5} className="bg-muted/30 p-4">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Product</TableHead>
                              <TableHead>Status</TableHead>
                              <TableHead>Contract</TableHead>
                              <TableHead className="text-right">Link</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {org.products.map((product) => (
                              <ProductRow
                                key={product.id}
                                product={product}
                                contracts={contracts[org.id] || []}
                                saving={saving}
                                onLink={(productId, contractId) =>
                                  linkContract(org.id, productId, contractId)
                                }
                              />
                            ))}
                            {!org.products.length && (
                              <TableRow>
                                <TableCell
                                  colSpan={4}
                                  className="h-16 text-center text-muted-foreground"
                                >
                                  This organization has no products.
                                </TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))
            )}
            {!loading && !organizations.length && (
              <TableRow>
                <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                  No organizations match this filter.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={loading || pagination.page <= 1}
          onClick={() => load(pagination.page - 1, filter, search)}
        >
          Previous
        </Button>
        <span className="text-[12px] text-muted-foreground">
          Page {pagination.page} of {Math.max(1, Math.ceil(pagination.total / pagination.pageSize))}
          {" · "}
          {pagination.total} organization(s)
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={loading || !pagination.hasNext}
          onClick={() => load(pagination.page + 1, filter, search)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
