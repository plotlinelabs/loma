"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  createIntegrationActivity,
  fetchIntegrationAccount,
  createIntegrationProject,
  createIntegrationSourceLink,
  createIntegrationWorkItem,
  createIntegrationSyncSource,
  searchPylonCustomers,
  fetchPylonIssue,
  fetchPylonIssues,
  archiveIntegrationProject,
  archiveIntegrationSourceLink,
  archiveIntegrationWorkItem,
  formatIntegrationLabel,
  INTEGRATION_HEALTH,
  IntegrationAccount,
  IntegrationSourceLink, IntegrationWorkItemInput, PylonCustomerMatch,
  PylonIssueDetail, PylonIssueSummary,
  IntegrationWorkItemType,
  updateIntegrationAccount,
  updateIntegrationWorkItem,
  createIntegrationContact, updateIntegrationContact, provisionIntegrationContact,
  revokeIntegrationContactAccess,
  archiveIntegrationContact,
  discoverGrainMeetings, fetchGrainMeetingSummary, GrainMeeting,
} from "@/lib/integration-hub-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { RiAddLine, RiDeleteBinLine, RiExternalLinkLine, RiEditLine } from "@remixicon/react";

const TYPES: IntegrationWorkItemType[] = ["milestone", "task", "risk", "blocker"];
const PLATFORMS = ["android", "ios", "react_native", "flutter", "web", "unity", "kmp"];
const ENVIRONMENTS = ["development", "staging", "production"];
const INTERNAL_EMAIL_DOMAIN = (
  process.env.NEXT_PUBLIC_INTERNAL_EMAIL_DOMAIN || ["plot", "line.so"].join("")
).toLowerCase();
const MEETING_RECORDER = process.env.NEXT_PUBLIC_MEETING_RECORDER_EMAIL || `meetings@${INTERNAL_EMAIL_DOMAIN}`;
const ACCESS_DURATIONS = [1, 3, 5, 7, 14] as const;

const emptyItem: IntegrationWorkItemInput = {
  type: "task",
  title: "",
  description: null,
  status: "todo",
  owner_email: null,
  due_at: null,
  severity: null,
  dependency: null,
  resolution: null,
  escalated: false,
};

function MeetingScheduler({ account }: { account: IntegrationAccount }) {
  const [selected, setSelected] = useState<string[]>([]);
  const attendees = Array.from(new Set([...selected, MEETING_RECORDER]));
  const url = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(`Call with ${account.name}`)}&add=${encodeURIComponent(attendees.join(","))}`;
  return <div className="mt-5 rounded-lg border p-3">
    <p className="text-sm font-medium">Schedule a client call</p>
    <p className="text-xs text-muted-foreground">Choose attendees. `{MEETING_RECORDER}` is always included for recording.</p>
    <div className="mt-2 flex flex-wrap gap-2">
      {(account.contacts || []).map((item) => <label key={item.contact_id} className="flex items-center gap-2 rounded-md border px-2 py-1 text-xs">
        <input type="checkbox" checked={selected.includes(item.email)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, item.email] : current.filter((email) => email !== item.email))} />
        {item.name}
      </label>)}
    </div>
    <a className="mt-3 inline-flex text-sm text-primary underline" target="_blank" rel="noreferrer" href={url}>Continue to Google Calendar</a>
  </div>;
}

export default function OnboardingWorkspace({
  account,
  onChange,
  section = "onboarding",
}: {
  account: IntegrationAccount;
  onChange: (account: IntegrationAccount) => void;
  section?: "onboarding" | "communications" | "contacts" | "history";
}) {
  const [item, setItem] = useState(emptyItem);
  const [editingItem, setEditingItem] = useState<IntegrationAccount["work_items"][number] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activityType, setActivityType] = useState<"note" | "decision">("note");
  const [activityMessage, setActivityMessage] = useState("");
  const [source, setSource] = useState<Pick<IntegrationSourceLink, "type" | "title" | "url" | "notes">>({
    type: "document", title: "", url: "", notes: null,
  });
  const [projectName, setProjectName] = useState("");
  const [projectPlaybook, setProjectPlaybook] = useState("mobile_sdk");
  const [pylonQuery, setPylonQuery] = useState("");
  const [pylonMatches, setPylonMatches] = useState<PylonCustomerMatch[]>([]);
  const [searchingPylon, setSearchingPylon] = useState(false);
  const [pylonSearchCompleted, setPylonSearchCompleted] = useState(false);
  const [pylonIssues, setPylonIssues] = useState<PylonIssueSummary[]>([]);
  const [urgentIssues, setUrgentIssues] = useState<PylonIssueSummary[]>([]);
  const [pylonIssueCursor, setPylonIssueCursor] = useState<string | null>(null);
  const [pylonIssueQuery, setPylonIssueQuery] = useState("");
  const [pylonIssueStatus, setPylonIssueStatus] = useState("waiting_on_customer");
  const [loadingPylonIssues, setLoadingPylonIssues] = useState(false);
  const [selectedPylonIssue, setSelectedPylonIssue] = useState<PylonIssueDetail | null>(null);
  const [loadingPylonIssueId, setLoadingPylonIssueId] = useState<string | null>(null);
  const autoSearchedPylon = useRef(false);
  const pylonIssueRequest = useRef(0);
  const pylonConnected = (account.sync_sources || []).some((source) => source.source === "pylon");
  const emptyContact = { name: "", email: "", role: "", role_description: "", phone: "", dashboard_access: "", access_duration_days: "", organization_id: "", product_ids: "" };
  const [contact, setContact] = useState(emptyContact);
  const [editingContactId, setEditingContactId] = useState<string | null>(null);
  const [domainInput, setDomainInput] = useState((account.client_email_domains || []).join(", "));
  const [approvedProductsInput, setApprovedProductsInput] = useState(
    (account.approved_product_ids || []).join(", ")
  );
  const [grainMeetings, setGrainMeetings] = useState<GrainMeeting[]>([]);
  const [loadingGrain, setLoadingGrain] = useState(false);
  const [grainSummary, setGrainSummary] = useState<{ id: string; summary: string; transcript: string; actionItems: GrainMeeting["action_items"] } | null>(null);
  const [grainSummaryMode, setGrainSummaryMode] = useState<"grain" | "loma">("grain");
  const [loadingGrainSummaryId, setLoadingGrainSummaryId] = useState<string | null>(null);

  function pylonAssignee(issue: PylonIssueSummary) {
    if (typeof issue.assignee === "string") return "Assigned user";
    return issue.assignee?.name || issue.assignee?.email || (issue.assignee?.id ? "Assigned user" : "Unassigned");
  }

  async function loadPylonIssues(cursor?: string, append = false) {
    const requestId = ++pylonIssueRequest.current;
    setLoadingPylonIssues(true); setError(null);
    try {
      const result = await fetchPylonIssues(account.account_id, {
        cursor,
        status: pylonIssueStatus,
        query: pylonIssueQuery.trim() || undefined, limit: 25,
      });
      if (requestId !== pylonIssueRequest.current) return;
      setPylonIssues((current) => append ? [...current, ...result.issues] : result.issues);
      setPylonIssueCursor(result.pagination.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load Pylon issues");
    } finally {
      if (requestId === pylonIssueRequest.current) setLoadingPylonIssues(false);
    }
  }

  async function loadUrgentPylonIssues() {
    try {
      const result = await fetchPylonIssues(account.account_id, {
        status: "new,waiting_on_you", limit: 25,
      });
      setUrgentIssues(result.issues);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load urgent Pylon issues");
    }
  }

  async function openPylonIssue(issueId: string) {
    setLoadingPylonIssueId(issueId); setError(null);
    try { setSelectedPylonIssue(await fetchPylonIssue(account.account_id, issueId)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load Pylon issue"); }
    finally { setLoadingPylonIssueId(null); }
  }

  useEffect(() => {
    if (!pylonConnected) return;
    setPylonIssues([]);
    setPylonIssueCursor(null);
    setSelectedPylonIssue(null);
    const timer = window.setTimeout(() => { void loadPylonIssues(); }, 300);
    return () => window.clearTimeout(timer);
  // Search/filter changes intentionally trigger a debounced server-side fetch.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.account_id, pylonConnected, pylonIssueQuery, pylonIssueStatus]);

  useEffect(() => {
    if (pylonConnected) void loadUrgentPylonIssues();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.account_id, pylonConnected]);

  async function findPylonCustomers() {
    setSearchingPylon(true); setPylonSearchCompleted(false); setError(null);
    try {
      const result = await searchPylonCustomers(pylonQuery);
      setPylonMatches(result.customers);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not search Pylon customers"); }
    finally { setSearchingPylon(false); setPylonSearchCompleted(true); }
  }

  useEffect(() => {
    if (pylonConnected || autoSearchedPylon.current) return;
    autoSearchedPylon.current = true;
    setPylonQuery(account.name);
    setSearchingPylon(true); setPylonSearchCompleted(false); setError(null);
    void searchPylonCustomers(account.name)
      .then((result) => {
        setPylonMatches(result.customers);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not identify this client in Pylon"))
      .finally(() => { setSearchingPylon(false); setPylonSearchCompleted(true); });
  // Discovery is automatic, but creating a persistent mapping requires an explicit click.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.name, pylonConnected]);

  async function connectPylon(customer: PylonCustomerMatch) {
    setSaving(true); setError(null);
    try {
      await createIntegrationSyncSource(account.account_id, {
        source: "pylon", tenant_id: "pylon", external_id: customer.customer_id,
        label: customer.name, config: { customer_name: customer.name },
      });
      const refreshed = await fetchIntegrationAccount(account.account_id);
      onChange(refreshed.account); setPylonMatches([]); setPylonQuery("");
    } catch (err) { setError(err instanceof Error ? err.message : "Could not connect Pylon customer"); }
    finally { setSaving(false); }
  }

  async function addContact(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null);
    try {
      const payload = {
        ...contact,
        role: contact.role || null, role_description: contact.role_description || null,
        phone: contact.phone || null, dashboard_access: contact.dashboard_access || null,
        access_duration_days: contact.access_duration_days ? Number(contact.access_duration_days) as 1 | 3 | 5 | 7 | 14 : null,
        organization_id: contact.organization_id || null,
        product_ids: contact.product_ids.split(",").map((item) => item.trim()).filter(Boolean),
      };
      const result = editingContactId
        ? await updateIntegrationContact(account.account_id, editingContactId, account.version, payload)
        : await createIntegrationContact(account.account_id, account.version, payload);
      onChange(result.account);
      setContact(emptyContact); setEditingContactId(null);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not add contact"); }
    finally { setSaving(false); }
  }

  function editContact(item: IntegrationAccount["contacts"][number]) {
    setEditingContactId(item.contact_id);
    setContact({
      name: item.name, email: item.email, role: item.role || "",
      role_description: item.role_description || "", phone: item.phone || "",
      dashboard_access: item.dashboard_access || "",
      access_duration_days: item.access_duration_days ? String(item.access_duration_days) : "",
      organization_id: item.organization_id || "",
      product_ids: (item.product_ids || []).join(", "),
    });
  }

  async function saveDomains() {
    setSaving(true); setError(null);
    try {
      const domains = domainInput.split(",").map((item) => item.trim().replace(/^@/, "")).filter(Boolean);
      const approvedProducts = approvedProductsInput.split(",").map((item) => item.trim()).filter(Boolean);
      const result = await updateIntegrationAccount(account.account_id, {
        name: account.name, client_email_domains: domains,
        approved_product_ids: approvedProducts,
      }, account.version);
      onChange(result.account);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not save client email domains"); }
    finally { setSaving(false); }
  }

  async function provisionAccess(contactId: string) {
    setSaving(true); setError(null);
    try {
      if (!window.confirm("Grant access to the selected approved products?")) return;
      const result = await provisionIntegrationContact(account.account_id, contactId, account.version);
      onChange(result.account);
      if (result.failures.length) {
        setError(`Access failed for: ${result.failures.map((item) => item.product_id).join(", ")}`);
      }
    } catch (err) { setError(err instanceof Error ? err.message : "Could not grant dashboard access"); }
    finally { setSaving(false); }
  }

  async function revokeAccess(contactId: string) {
    setSaving(true); setError(null);
    try {
      if (!window.confirm("Revoke access to the selected approved products?")) return;
      const result = await revokeIntegrationContactAccess(account.account_id, contactId, account.version);
      onChange(result.account);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not revoke dashboard access"); }
    finally { setSaving(false); }
  }

  async function removeContact(contactId: string) {
    setSaving(true); setError(null);
    try {
      if (!window.confirm("Remove this client user?")) return;
      const result = await archiveIntegrationContact(account.account_id, contactId, account.version);
      onChange(result.account);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not remove contact"); }
    finally { setSaving(false); }
  }

  async function findGrainMeetings() {
    setLoadingGrain(true); setError(null);
    try { setGrainMeetings((await discoverGrainMeetings(account.account_id)).recordings); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not search Grain"); }
    finally { setLoadingGrain(false); }
  }

  useEffect(() => {
    if (section !== "communications") return;
    void findGrainMeetings();
  // Refresh Grain automatically whenever the communications workspace is opened.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section, account.account_id, account.contacts?.length]);

  async function showGrainSummary(meeting: GrainMeeting) {
    if (grainSummary?.id === meeting.id) {
      setGrainSummary(null);
      return;
    }
    setLoadingGrainSummaryId(meeting.id); setError(null);
    try {
      const result = await fetchGrainMeetingSummary(account.account_id, meeting.id);
      setGrainSummary({
        id: meeting.id, summary: result.summary,
        transcript: result.transcript_excerpt, actionItems: result.action_items,
      });
    } catch (err) { setError(err instanceof Error ? err.message : "Could not load Grain summary"); }
    finally { setLoadingGrainSummaryId(null); }
  }

  async function addProject(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const data = await createIntegrationProject(account.account_id, account.version, {
        name: projectName, playbook: projectPlaybook,
      });
      onChange(data.account);
      setProjectName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add project");
    } finally {
      setSaving(false);
    }
  }

  async function removeProject(projectId: string, name: string) {
    if (!window.confirm(`Delete project "${name}"?`)) return;
    setError(null);
    try {
      const project = account.projects.find((entry) => entry.project_id === projectId);
      const data = await archiveIntegrationProject(
        account.account_id, projectId, project?.version || 0,
      );
      onChange(data.account);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete project");
    }
  }

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
        health_override_enabled: values.get("health_override_enabled") === "on",
        health: String(values.get("health") || account.health) as IntegrationAccount["health"],
        health_reason: String(values.get("health_reason") || ""),
      }, account.version);
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
      const data = await createIntegrationWorkItem(account.account_id, account.version, item);
      onChange(data.account);
      setItem(emptyItem);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add item");
    } finally {
      setSaving(false);
    }
  }

  async function setStatus(itemId: string, status: IntegrationWorkItemInput["status"]) {
    setError(null);
    try {
      const data = await updateIntegrationWorkItem(account.account_id, itemId, account.work_items.find((entry) => entry.item_id === itemId)?.version || 0, { status });
      onChange(data.account);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update status"); }
  }

  async function removeItem(itemId: string) {
    setError(null);
    try {
      const data = await archiveIntegrationWorkItem(account.account_id, itemId, account.work_items.find((entry) => entry.item_id === itemId)?.version || 0);
      onChange(data.account);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not archive work item"); }
  }

  async function saveEditedItem(event: FormEvent) {
    event.preventDefault();
    if (!editingItem) return;
    setSaving(true); setError(null);
    try {
      const data = await updateIntegrationWorkItem(account.account_id, editingItem.item_id, editingItem.version, {
        title: editingItem.title,
        description: editingItem.description || null,
        owner_email: editingItem.owner_email || null,
        due_at: editingItem.due_at?.slice(0, 10) || null,
        dependency: editingItem.dependency || null,
        resolution: editingItem.resolution || null,
      });
      onChange(data.account);
      setEditingItem(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update work item");
    } finally { setSaving(false); }
  }

  async function addActivity(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const data = await createIntegrationActivity(account.account_id, account.version, {
        type: activityType, message: activityMessage,
      });
      onChange(data.account);
      setActivityMessage("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add activity");
    } finally {
      setSaving(false);
    }
  }

  async function addSource(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const data = await createIntegrationSourceLink(account.account_id, account.version, source);
      onChange(data.account);
      setSource({ type: "document", title: "", url: "", notes: null });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add source");
    } finally {
      setSaving(false);
    }
  }

  async function removeSource(link: IntegrationSourceLink) {
    if (!window.confirm(`Delete source "${link.title}"?`)) return;
    setError(null);
    try {
      const data = await archiveIntegrationSourceLink(
        account.account_id, link.link_id, link.version,
      );
      onChange(data.account);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete source");
    }
  }


  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Card className={section === "communications" ? "p-4" : "hidden"}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="font-heading text-base font-medium">Communication monitoring</h2>
            <p className="text-xs text-muted-foreground">
              Read-only activity imported from connected customer communication sources.
            </p>
          </div>
        </div>

        {!pylonConnected && <div className="mt-3 rounded-lg border p-3">
          <p className="text-sm font-medium">Connect a Pylon customer</p>
          <p className="text-xs text-muted-foreground">Search and map one customer. Only that customer&apos;s issues and full message threads are imported.</p>
          <div className="mt-2 flex gap-2">
            <Input maxLength={100} placeholder="Client name in Pylon" value={pylonQuery} onChange={(event) => setPylonQuery(event.target.value)} />
            <Button type="button" variant="outline" size="sm" disabled={searchingPylon || !pylonQuery.trim()} onClick={findPylonCustomers}>{searchingPylon ? "Searching..." : "Search Pylon"}</Button>
          </div>
          <div className="mt-2 space-y-2">
            {pylonMatches.map((customer) => (
              <div key={customer.customer_id} className="flex items-center justify-between gap-3 rounded-md bg-muted/40 p-2">
                <div><p className="text-sm font-medium">{customer.name}</p><p className="text-xs text-muted-foreground">{customer.domains?.length ? customer.domains.join(", ") : "Pylon customer account"}</p></div>
                <Button type="button" size="sm" disabled={saving} onClick={() => connectPylon(customer)}>Connect read-only</Button>
              </div>
            ))}
            {pylonSearchCompleted && !pylonMatches.length && !error ? (
              <p className="text-sm text-muted-foreground">No Pylon customers found for &quot;{pylonQuery}&quot;.</p>
            ) : null}
          </div>
        </div>}
        {pylonConnected && urgentIssues.length > 0 && <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50/40 p-3">
          <div className="mb-2">
            <p className="text-sm font-medium">Needs a our response</p>
            <p className="text-xs text-muted-foreground">New issues and conversations currently waiting on us.</p>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {urgentIssues.map((issue) => (
              <div key={issue.id} className="rounded-md border bg-background p-3">
                <div className="flex items-start justify-between gap-2">
                  <a href={issue.url || "#"} target="_blank" rel="noreferrer" className="font-medium text-primary underline">
                    {issue.title}
                  </a>
                  <Badge variant="outline">{formatIntegrationLabel(issue.state)}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {pylonAssignee(issue)} · {issue.updated_at ? new Date(issue.updated_at).toLocaleString() : "Unknown update time"}
                </p>
                <div className="mt-2 flex gap-2">
                  <Button type="button" variant="outline" size="sm" disabled={loadingPylonIssueId === issue.id} onClick={() => openPylonIssue(issue.id)}>
                    {loadingPylonIssueId === issue.id ? "Loading..." : "View context"}
                  </Button>
                  {issue.url && <a href={issue.url} target="_blank" rel="noreferrer" className="inline-flex items-center text-xs text-primary underline">Open in Pylon</a>}
                </div>
              </div>
            ))}
          </div>
        </div>}
        {selectedPylonIssue && <div className="mt-3 rounded-lg border p-4">
          <div className="flex items-start justify-between gap-3">
            <div><p className="font-medium">{selectedPylonIssue.issue.title}</p><p className="text-xs text-muted-foreground">{selectedPylonIssue.messages.length} messages loaded on demand</p></div>
            <Button type="button" variant="ghost" size="sm" onClick={() => setSelectedPylonIssue(null)}>Close</Button>
          </div>
          <div className="mt-3 max-h-96 space-y-2 overflow-y-auto">
            {selectedPylonIssue.messages.map((message, index) => <div key={message.id || String(index)} className="rounded-md bg-muted/40 p-3 text-sm">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{message.author}{message.is_private ? " · Internal note" : ""}</span>
                <span>{message.timestamp ? new Date(message.timestamp).toLocaleString() : ""}</span>
              </div>
              <p className="whitespace-pre-wrap">{message.body || "Message content unavailable"}</p>
              {message.attachments?.length > 0 && <div className="mt-3 flex flex-wrap gap-2">
                {message.attachments.map((attachment) => attachment.is_image ? (
                  <a key={attachment.url} href={attachment.url} target="_blank" rel="noreferrer" className="block">
                    {/* Pylon returns signed, read-only asset URLs from its own CDN. */}
                    <img src={attachment.url} alt={attachment.name} className="max-h-48 max-w-full rounded-md border object-contain" />
                  </a>
                ) : (
                  <a key={attachment.url} href={attachment.url} target="_blank" rel="noreferrer" className="inline-flex rounded-md border px-3 py-2 text-xs text-primary underline">
                    {attachment.name}
                  </a>
                ))}
              </div>}
            </div>)}
          </div>
          {selectedPylonIssue.issue.url && <a className="mt-3 inline-block text-sm text-primary underline" href={selectedPylonIssue.issue.url} target="_blank" rel="noreferrer">View ticket in Pylon</a>}
        </div>}
        {pylonConnected && <div className="mt-4 rounded-lg border">
          <div className="flex flex-wrap gap-2 border-b p-3">
            <Input className="max-w-sm" maxLength={100} placeholder="Search issues" value={pylonIssueQuery} onChange={(event) => setPylonIssueQuery(event.target.value)} />
            <select className="h-9 rounded-md border bg-background px-3 text-sm" value={pylonIssueStatus} onChange={(event) => setPylonIssueStatus(event.target.value)}>
              <option value="new">New</option>
              <option value="waiting_on_you">On us</option>
              <option value="waiting_on_customer">On customer</option>
              <option value="on_hold">On hold</option>
              <option value="closed">Closed</option>
            </select>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b bg-muted/40 text-xs text-muted-foreground"><tr><th className="p-3">Issue</th><th className="p-3">Status</th><th className="p-3">Assignee</th><th className="p-3">Updated</th><th className="p-3">Ticket</th></tr></thead>
              <tbody>
                {pylonIssues.map((issue) => <tr key={issue.id} className="border-b hover:bg-muted/30">
                  <td className="p-3 font-medium">
                    {issue.url ? <a href={issue.url} target="_blank" rel="noreferrer" className="text-primary underline">{issue.title}</a> : issue.title}
                  </td>
                  <td className="p-3"><Badge variant="outline">{formatIntegrationLabel(issue.state)}</Badge></td>
                  <td className="p-3">{pylonAssignee(issue)}</td>
                  <td className="p-3 text-muted-foreground">{issue.updated_at ? new Date(issue.updated_at).toLocaleString() : "Unknown"}</td>
                  <td className="p-3"><div className="flex gap-2">{issue.url ? <a href={issue.url} target="_blank" rel="noreferrer" className="text-primary underline">Open</a> : "Unavailable"}<button type="button" className="text-primary underline" onClick={() => openPylonIssue(issue.id)}>Details</button></div></td>
                </tr>)}
              </tbody>
            </table>
            {!pylonIssues.length && !loadingPylonIssues && <p className="p-4 text-sm text-muted-foreground">No issues match this status and search.</p>}
          </div>
          {pylonIssueCursor && <div className="border-t p-3"><Button type="button" variant="outline" size="sm" disabled={loadingPylonIssues} onClick={() => loadPylonIssues(pylonIssueCursor, true)}>{loadingPylonIssues ? "Loading..." : "Load more"}</Button></div>}
        </div>}
      </Card>
      <Card className={section === "contacts" ? "p-4" : "hidden"}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-heading text-base font-medium">Client and Internal users</h2>
            <p className="text-xs text-muted-foreground">Manage pilot stakeholders, dashboard access, organization IDs, invitations, and meeting attendees.</p>
          </div>
        </div>
        <div className="mt-3 rounded-lg border p-3">
          <Label>Allowed client email domains</Label>
          <div className="mt-2 flex gap-2">
            <Input placeholder="client.com, subsidiary.com" value={domainInput} onChange={(event) => setDomainInput(event.target.value)} />
            <Button type="button" variant="outline" size="sm" disabled={saving} onClick={saveDomains}>Save domains</Button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">Only configured client domains can be stored. Internal users are grouped separately.</p>
          <Label className="mt-3 block">Approved product IDs</Label>
          <Input
            className="mt-2"
            placeholder="product-id-1, product-id-2"
            value={approvedProductsInput}
            onChange={(event) => setApprovedProductsInput(event.target.value)}
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Access can only be granted to products explicitly registered for this client.
          </p>
        </div>
        <form onSubmit={addContact} className="mt-3 grid gap-2 md:grid-cols-2">
          <Input required placeholder="Name" value={contact.name} onChange={(event) => setContact({ ...contact, name: event.target.value })} />
          <Input required type="email" placeholder="Email" value={contact.email} onChange={(event) => setContact({ ...contact, email: event.target.value })} />
          {contact.email.includes("@") && <p className="text-xs text-muted-foreground md:col-span-2">
            {contact.email.toLowerCase().endsWith(`@${INTERNAL_EMAIL_DOMAIN}`)
              ? "Recognized as an Internal user"
              : "Recognized as a Client user"}
          </p>}
          <Input placeholder="Role or title" value={contact.role} onChange={(event) => setContact({ ...contact, role: event.target.value })} />
          <Input placeholder="Phone" value={contact.phone} onChange={(event) => setContact({ ...contact, phone: event.target.value })} />
          <Textarea placeholder="Role in the pilot and why this person matters" value={contact.role_description} onChange={(event) => setContact({ ...contact, role_description: event.target.value })} />
          <div className="space-y-2">
            <Label htmlFor="dashboard-access-role">Dashboard access</Label>
            <Select value={contact.dashboard_access || "none"} onValueChange={(value) => setContact({ ...contact, dashboard_access: value === "none" ? "" : value, access_duration_days: value === "none" ? "" : contact.access_duration_days })}>
              <SelectTrigger id="dashboard-access-role" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Stakeholder only, no dashboard access</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
                <SelectItem value="publisher">Publisher</SelectItem>
                <SelectItem value="viewer">Viewer</SelectItem>
              </SelectContent>
            </Select>
            {contact.dashboard_access && contact.email.toLowerCase().endsWith(`@${INTERNAL_EMAIL_DOMAIN}`) && <>
              <Label htmlFor="dashboard-access-duration">Access duration</Label>
              <Select value={contact.access_duration_days || undefined} onValueChange={(value) => setContact({ ...contact, access_duration_days: value })}>
                <SelectTrigger id="dashboard-access-duration" className="w-full"><SelectValue placeholder="Select duration" /></SelectTrigger>
                <SelectContent>
                  {ACCESS_DURATIONS.map((days) => <SelectItem key={days} value={String(days)}>{days} day{days === 1 ? "" : "s"}</SelectItem>)}
                </SelectContent>
              </Select>
            </>}
            <Input placeholder="Organization ID" value={contact.organization_id} onChange={(event) => setContact({ ...contact, organization_id: event.target.value })} />
            <Input placeholder="Product IDs, comma separated" value={contact.product_ids} onChange={(event) => setContact({ ...contact, product_ids: event.target.value })} />
            {contact.dashboard_access && !contact.email.toLowerCase().endsWith(`@${INTERNAL_EMAIL_DOMAIN}`) &&
              <p className="text-xs text-muted-foreground">Client access is permanent until manually revoked.</p>}
          </div>
          <div className="flex gap-2 md:col-span-2">
            <Button type="submit" size="sm" disabled={saving}>{editingContactId ? "Save user" : "Add user"}</Button>
            {editingContactId && <Button type="button" variant="outline" size="sm" onClick={() => { setEditingContactId(null); setContact(emptyContact); }}>Cancel</Button>}
          </div>
        </form>
        {(["client", "internal"] as const).map((bucket) => {
          const items = (account.contacts || []).filter((item) => item.email.endsWith(`@${INTERNAL_EMAIL_DOMAIN}`) === (bucket === "internal"));
          return <div key={bucket} className="mt-5">
            <h3 className="text-sm font-medium">{bucket === "internal" ? "Internal users" : "Client users"}</h3>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
            {items.map((item) => (
            <div key={item.contact_id} className="flex items-center justify-between rounded-lg border p-3">
              <div className="min-w-0">
                <p className="text-sm font-medium">{item.name}</p>
                <a href={`mailto:${item.email}`} className="text-xs text-primary underline">{item.email}</a>
                {item.role && <p className="text-xs">{item.role}</p>}
                {item.role_description && <p className="text-xs text-muted-foreground">{item.role_description}</p>}
                <p className="mt-1 text-xs text-muted-foreground">
                  Access: {item.dashboard_access || "Not configured"}
                  {item.access_duration_days ? ` · ${item.access_duration_days} days` : ""}
                  {(item.product_ids || []).length ? ` · Products: ${item.product_ids.join(", ")}` : ""}
                </p>
                {item.access_expires_at && <p className="text-xs text-muted-foreground">Expires {new Date(item.access_expires_at).toLocaleString()}</p>}
                {item.access_status && <p className={`text-xs ${["active", "revoked", "expired"].includes(item.access_status) ? "text-emerald-600" : "text-destructive"}`}>Provisioning: {formatIntegrationLabel(item.access_status)}</p>}
              </div>
              <div className="flex gap-1">
                {item.dashboard_access && !["provisioning", "active", "partially_granted", "revoking", "revocation_failed"].includes(item.access_status || "") && <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => provisionAccess(item.contact_id)}>Grant access</Button>}
                {["active", "partially_granted", "revoking", "revocation_failed"].includes(item.access_status || "") && <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => revokeAccess(item.contact_id)}>Revoke access</Button>}
                <Button type="button" variant="ghost" size="icon-sm" aria-label={`Edit ${item.name}`} onClick={() => editContact(item)}><RiEditLine /></Button>
                <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remove ${item.name}`} onClick={() => removeContact(item.contact_id)}><RiDeleteBinLine /></Button>
              </div>
            </div>
            ))}
            {!items.length && <p className="text-xs text-muted-foreground">No {bucket} users added.</p>}
            </div>
          </div>;
        })}
        {(account.contacts || []).length > 0 && <MeetingScheduler account={account} />}
      </Card>
      <Card className={section === "communications" ? "p-4" : "hidden"}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-heading text-base font-medium">Grain meetings</h2>
            <p className="text-xs text-muted-foreground">Find meetings using the client name and saved contact domains. Summaries load only when requested.</p>
          </div>
          <Button type="button" variant="outline" size="sm" disabled={loadingGrain} onClick={findGrainMeetings}>{loadingGrain ? "Searching..." : "Find meetings"}</Button>
        </div>
        <div className="mt-3 space-y-2">
          {grainMeetings.map((meeting) => (
            <div key={meeting.id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div><a href={meeting.url} target="_blank" rel="noreferrer" className="text-sm font-medium text-primary underline">{meeting.title}</a><p className="text-xs text-muted-foreground">{meeting.date ? new Date(meeting.date).toLocaleString() : "Meeting date unavailable"} · {(meeting.participants || []).map((item) => item.name || item.email).join(", ") || "No participants listed"}</p></div>
                <Button type="button" variant="outline" size="sm" disabled={loadingGrainSummaryId === meeting.id} onClick={() => showGrainSummary(meeting)}>
                  {loadingGrainSummaryId === meeting.id ? "Loading..." : grainSummary?.id === meeting.id ? "Hide summary" : "Show summary"}
                </Button>
              </div>
              {grainSummary?.id === meeting.id && <div className="mt-3 rounded-md bg-muted/40 p-3 text-sm">
                <div className="mb-3 inline-flex rounded-md border bg-background p-0.5">
                  {(["grain", "loma"] as const).map((mode) => <button key={mode} type="button" onClick={() => setGrainSummaryMode(mode)} className={`rounded px-2 py-1 text-xs ${grainSummaryMode === mode ? "bg-muted font-medium" : ""}`}>{mode === "grain" ? "Grain summary" : "Loma summary"}</button>)}
                </div>
                {grainSummaryMode === "grain"
                  ? <p className="whitespace-pre-wrap">{grainSummary.summary}</p>
                  : <div className="space-y-2">
                      <p className="font-medium">Onboarding-focused review</p>
                      <p className="whitespace-pre-wrap">{grainSummary.summary}</p>
                      <p className="text-xs text-muted-foreground">This view combines the meeting summary, action items, and transcript evidence. AI regeneration will be enabled only after the approved model and cost policy are configured.</p>
                    </div>}
                {grainSummary.actionItems?.length ? <ul className="mt-2 list-disc pl-4">{grainSummary.actionItems.map((item, index) => <li key={`${meeting.id}-${index}`}>{item.text}</li>)}</ul> : null}
                {grainSummary.transcript && <details className="mt-2"><summary className="cursor-pointer text-xs text-primary">Transcript excerpt</summary><p className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap text-xs">{grainSummary.transcript}</p></details>}
              </div>}
            </div>
          ))}
          {!loadingGrain && grainMeetings.length === 0 && <p className="text-xs text-muted-foreground">Search to find Grain meetings for this client.</p>}
        </div>
      </Card>
      <Card className={section === "onboarding" ? "p-4" : "hidden"}>
        <h2 className="font-heading text-base font-medium">Projects and playbooks</h2>
        <p className="text-xs text-muted-foreground">Track parallel workstreams and create standard onboarding tasks from a reusable playbook.</p>
        <form onSubmit={addProject} className="mt-4 grid gap-2 sm:grid-cols-[1fr_220px_auto]">
          <Input required maxLength={200} placeholder="Project or workstream name" value={projectName} onChange={(event) => setProjectName(event.target.value)} />
          <select className="h-9 rounded-md border bg-background px-3 text-sm" value={projectPlaybook} onChange={(event) => setProjectPlaybook(event.target.value)}>
            <option value="mobile_sdk">Mobile SDK onboarding</option>
            <option value="web_sdk">Web SDK onboarding</option>
            <option value="">No playbook</option>
          </select>
          <Button type="submit" size="sm" disabled={saving || !projectName.trim()}><RiAddLine />Add project</Button>
        </form>
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {(account.projects || []).map((project) => (
            <div key={project.project_id} className="rounded-lg border p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium">{project.name}</p>
                <div className="flex items-center gap-1">
                  <Badge variant="secondary">{formatIntegrationLabel(project.status)}</Badge>
                  <Button variant="ghost" size="icon-sm" onClick={() => removeProject(project.project_id, project.name)} aria-label={`Delete ${project.name}`}><RiDeleteBinLine /></Button>
                </div>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{project.playbook ? formatIntegrationLabel(project.playbook) : "Custom workstream"}</p>
            </div>
          ))}
          {(account.projects || []).length === 0 && <p className="text-xs text-muted-foreground">No projects yet.</p>}
        </div>
      </Card>
      <Card className={section === "onboarding" ? "p-4" : "hidden"}>
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
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <Label>Automated health</Label>
                  <p className="text-xs text-muted-foreground">{formatIntegrationLabel(account.calculated_health)}</p>
                </div>
                <Badge variant="secondary">{account.calculated_health_reasons.length ? `${account.calculated_health_reasons.length} reason${account.calculated_health_reasons.length === 1 ? "" : "s"}` : "No risks"}</Badge>
              </div>
              {account.calculated_health_reasons.length > 0 && <ul className="mt-2 list-disc pl-4 text-xs text-muted-foreground">{account.calculated_health_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
            </div>
            <div className="rounded-lg border p-3">
              <label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" name="health_override_enabled" defaultChecked={account.health_override_enabled} />Override automated health</label>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <select name="health" defaultValue={account.health} className="h-9 rounded-md border bg-background px-2 text-xs">
                  {INTEGRATION_HEALTH.map((health) => <option key={health} value={health}>{formatIntegrationLabel(health)}</option>)}
                </select>
                <Input name="health_reason" defaultValue={account.health_reason || ""} placeholder="Override reason" />
              </div>
            </div>
          </div>
          <div className="flex items-end gap-3">
            <div className="w-40"><Label htmlFor="completion_percentage">Completion %</Label><Input id="completion_percentage" name="completion_percentage" type="number" min={0} max={100} defaultValue={account.completion_percentage || 0} /></div>
            <Button type="submit" size="sm" disabled={saving}>Save plan</Button>
          </div>
        </form>
      </Card>

      <Card className={section === "onboarding" ? "p-4" : "hidden"}>
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
                        <div className="min-w-48 flex-1"><p className="text-sm font-medium">{entry.title}</p><p className="text-xs text-muted-foreground">{entry.description ? `${entry.description} · ` : ""}{entry.owner_email || "Unassigned"}{entry.due_at ? ` · Due ${new Date(entry.due_at).toLocaleDateString()}` : ""}{entry.severity ? ` · ${formatIntegrationLabel(entry.severity)}` : ""}{entry.dependency ? ` · Dependency: ${entry.dependency}` : ""}{entry.resolution ? ` · Resolution: ${entry.resolution}` : ""}</p></div>
                        <select className="h-8 rounded-md border bg-background px-2 text-xs" value={entry.status} onChange={(event) => setStatus(entry.item_id, event.target.value as IntegrationWorkItemInput["status"])}>
                          {(entry.type === "task" ? ["todo", "in_progress", "blocked", "completed", "cancelled"] : entry.type === "milestone" ? ["pending", "in_progress", "achieved", "missed", "cancelled"] : entry.type === "risk" ? ["open", "mitigating", "accepted", "resolved"] : ["open", "mitigating", "resolved"]).map((status) => <option key={status} value={status}>{formatIntegrationLabel(status)}</option>)}
                        </select>
                        <Button variant="ghost" size="icon-sm" onClick={() => setEditingItem({ ...entry })} aria-label={`Edit ${entry.title}`}><RiEditLine /></Button>
                        <Button variant="ghost" size="icon-sm" onClick={() => removeItem(entry.item_id)} aria-label={`Delete ${entry.title}`}><RiDeleteBinLine /></Button>
                        {editingItem?.item_id === entry.item_id && (
                          <form onSubmit={saveEditedItem} className="grid w-full gap-2 rounded-md border bg-background p-3 sm:grid-cols-2 lg:grid-cols-3">
                            <div><Label>Title</Label><Input required value={editingItem.title} onChange={(event) => setEditingItem({ ...editingItem, title: event.target.value })} /></div>
                            <div><Label>Owner</Label><Input type="email" value={editingItem.owner_email || ""} onChange={(event) => setEditingItem({ ...editingItem, owner_email: event.target.value || null })} /></div>
                            <div><Label>Due date</Label><Input type="date" value={editingItem.due_at?.slice(0, 10) || ""} onChange={(event) => setEditingItem({ ...editingItem, due_at: event.target.value || null })} /></div>
                            <div className="sm:col-span-2 lg:col-span-3"><Label>Description</Label><Textarea value={editingItem.description || ""} onChange={(event) => setEditingItem({ ...editingItem, description: event.target.value || null })} /></div>
                            <div><Label>Dependency</Label><Input value={editingItem.dependency || ""} onChange={(event) => setEditingItem({ ...editingItem, dependency: event.target.value || null })} /></div>
                            <div><Label>Resolution</Label><Input value={editingItem.resolution || ""} onChange={(event) => setEditingItem({ ...editingItem, resolution: event.target.value || null })} /></div>
                            <div className="flex items-end justify-end gap-2"><Button type="button" variant="outline" size="sm" onClick={() => setEditingItem(null)}>Cancel</Button><Button type="submit" size="sm" disabled={saving || !editingItem.title.trim()}>Save</Button></div>
                          </form>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>
        <form onSubmit={addItem} className="mt-4 grid gap-3 rounded-lg border p-3 md:grid-cols-2 lg:grid-cols-4">
          <div><Label>Type</Label><select className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={item.type} onChange={(event) => { const type = event.target.value as IntegrationWorkItemType; const defaults = { task: "todo", milestone: "pending", risk: "open", blocker: "open" } as const; setItem({ ...item, type, status: defaults[type] }); }}>{TYPES.map((type) => <option key={type} value={type}>{formatIntegrationLabel(type)}</option>)}</select></div>
          <div><Label>Title</Label><Input required value={item.title} onChange={(event) => setItem({ ...item, title: event.target.value })} /></div>
          <div><Label>Owner</Label><Input type="email" value={item.owner_email || ""} onChange={(event) => setItem({ ...item, owner_email: event.target.value || null })} /></div>
          <div><Label>Due date</Label><Input type="date" value={item.due_at || ""} onChange={(event) => setItem({ ...item, due_at: event.target.value || null })} /></div>
          {(item.type === "risk" || item.type === "blocker") && <div><Label>Severity</Label><select className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={item.severity || ""} onChange={(event) => setItem({ ...item, severity: (event.target.value || null) as IntegrationWorkItemInput["severity"] })}><option value="">Not set</option>{["low", "medium", "high", "critical"].map((value) => <option key={value} value={value}>{formatIntegrationLabel(value)}</option>)}</select></div>}
          <div className="flex items-end"><Button type="submit" size="sm" disabled={saving || !item.title.trim()}><RiAddLine />Add item</Button></div>
        </form>
      </Card>

      <div className={section === "history" ? "grid gap-3 lg:grid-cols-2" : "hidden"}>
        <Card className="p-4">
          <h2 className="font-heading text-base font-medium">Activity timeline</h2>
          <p className="text-xs text-muted-foreground">Notes, decisions, and automatic onboarding changes.</p>
          <form onSubmit={addActivity} className="mt-4 flex gap-2">
            <select className="h-9 rounded-md border bg-background px-2 text-xs" value={activityType} onChange={(event) => setActivityType(event.target.value as "note" | "decision")}>
              <option value="note">Note</option><option value="decision">Decision</option>
            </select>
            <Input required maxLength={2000} placeholder="Add an update..." value={activityMessage} onChange={(event) => setActivityMessage(event.target.value)} />
            <Button type="submit" size="sm" disabled={saving || !activityMessage.trim()}><RiAddLine />Add</Button>
          </form>
          <div className="mt-4 max-h-80 space-y-2 overflow-y-auto">
            {[...(account.activities || [])].reverse().map((activity) => (
              <div key={activity.activity_id} className="rounded-lg border p-3">
                <div className="flex items-center justify-between gap-2"><Badge variant={activity.type === "update" ? "secondary" : "outline"}>{formatIntegrationLabel(activity.type)}</Badge><span className="text-[11px] text-muted-foreground">{new Date(activity.created_at).toLocaleString()}</span></div>
                <p className="mt-2 text-sm">{activity.message}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">{activity.created_by}</p>
              </div>
            ))}
            {(account.activities || []).length === 0 && <p className="text-xs text-muted-foreground">No activity yet.</p>}
          </div>
        </Card>

        <Card className="p-4">
          <h2 className="font-heading text-base font-medium">Source links</h2>
          <p className="text-xs text-muted-foreground">Attach meetings, threads, tickets, CRM records, and documents.</p>
          <form onSubmit={addSource} className="mt-4 grid gap-2 sm:grid-cols-2">
            <select className="h-9 rounded-md border bg-background px-2 text-xs" value={source.type} onChange={(event) => setSource({ ...source, type: event.target.value as IntegrationSourceLink["type"] })}>
              {["linear", "hubspot", "document", "other"].map((type) => <option key={type} value={type}>{formatIntegrationLabel(type)}</option>)}
            </select>
            <Input required placeholder="Title" value={source.title} onChange={(event) => setSource({ ...source, title: event.target.value })} />
            <Input required type="url" placeholder="https://..." value={source.url} onChange={(event) => setSource({ ...source, url: event.target.value })} />
            <Input placeholder="Optional notes" value={source.notes || ""} onChange={(event) => setSource({ ...source, notes: event.target.value || null })} />
            <Button type="submit" size="sm" className="sm:col-span-2" disabled={saving || !source.title.trim() || !source.url.trim()}><RiAddLine />Add source</Button>
          </form>
          <div className="mt-4 space-y-2">
            {(account.source_links || []).map((link) => (
              <div key={link.link_id} className="flex items-center gap-2 rounded-lg border p-3">
                <Badge variant="secondary">{formatIntegrationLabel(link.type)}</Badge>
                <a href={link.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-sm font-medium hover:underline">{link.title}</a>
                <RiExternalLinkLine className="text-muted-foreground" size={16} />
                <Button variant="ghost" size="icon-sm" onClick={() => removeSource(link)} aria-label={`Delete ${link.title}`}><RiDeleteBinLine /></Button>
              </div>
            ))}
            {(account.source_links || []).length === 0 && <p className="text-xs text-muted-foreground">No source links attached.</p>}
          </div>
        </Card>
      </div>
    </div>
  );
}
