export interface ToolMeta {
  displayName: string;
  authMethod: string;
  color: string;
  bgColor: string;
  supportsOAuth: boolean;
}

const TOOL_META: Record<string, ToolMeta> = {
  // Databases
  mongodb: {
    displayName: "MongoDB",
    authMethod: "Connection String",
    color: "#00684A",
    bgColor: "#ECFDF5",
    supportsOAuth: false,
  },
  clickhouse: {
    displayName: "ClickHouse",
    authMethod: "User / Password",
    color: "#FACC15",
    bgColor: "#FEFCE8",
    supportsOAuth: false,
  },
  bigquery: {
    displayName: "Google BigQuery",
    authMethod: "Service Account JSON",
    color: "#4285F4",
    bgColor: "#EFF6FF",
    supportsOAuth: false,
  },
  athena: {
    displayName: "AWS Athena",
    authMethod: "IAM Keys",
    color: "#FF9900",
    bgColor: "#FFF7ED",
    supportsOAuth: false,
  },

  github: {
    displayName: "GitHub",
    authMethod: "Bearer Token",
    color: "#24292F",
    bgColor: "#F6F8FA",
    supportsOAuth: true,
  },
  linear: {
    displayName: "Linear",
    authMethod: "Bearer Token",
    color: "#5E6AD2",
    bgColor: "#EEF2FF",
    supportsOAuth: true,
  },

  // CRM & Outreach
  hubspot: {
    displayName: "HubSpot",
    authMethod: "Access Token",
    color: "#FF7A59",
    bgColor: "#FFF1EE",
    supportsOAuth: true,
  },
  apollo: {
    displayName: "Apollo",
    authMethod: "API Key",
    color: "#6161CE",
    bgColor: "#F0EEFF",
    supportsOAuth: false,
  },
  phantombuster: {
    displayName: "PhantomBuster",
    authMethod: "API Key",
    color: "#7B3FE4",
    bgColor: "#F3EEFF",
    supportsOAuth: false,
  },

  // Docs & Knowledge
  "docs": {
    displayName: "GitBook",
    authMethod: "Cookie",
    color: "#3884FF",
    bgColor: "#EFF6FF",
    supportsOAuth: false,
  },
  notion: {
    displayName: "Notion",
    authMethod: "API Token",
    color: "#000000",
    bgColor: "#F7F7F7",
    supportsOAuth: true,
  },
  // Operations
  pylon: {
    displayName: "Pylon",
    authMethod: "API Key",
    color: "#0066FF",
    bgColor: "#EFF6FF",
    supportsOAuth: false,
  },
  grain: {
    displayName: "Grain",
    authMethod: "API Key",
    color: "#FF6B35",
    bgColor: "#FFF4EE",
    supportsOAuth: false,
  },
  dataroom: {
    displayName: "DataRoom",
    authMethod: "API Key",
    color: "#1A1A2E",
    bgColor: "#F1F1F5",
    supportsOAuth: false,
  },

  // Personal Integrations
  "google-personal": {
    displayName: "Google (Personal)",
    authMethod: "OAuth 2.0",
    color: "#4285F4",
    bgColor: "#EFF6FF",
    supportsOAuth: true,
  },
};

export function getToolMeta(name: string): ToolMeta {
  return (
    TOOL_META[name] || {
      displayName: name,
      authMethod: "Unknown",
      color: "#6B7280",
      bgColor: "#F9FAFB",
      supportsOAuth: false,
    }
  );
}

/* ── Categories ────────────────────────────────────────────────────── */

export interface Category {
  name: string;
  keys: string[];
}

export const CATEGORIES: Category[] = [
  { name: "Databases", keys: ["mongodb", "clickhouse", "bigquery", "athena"] },
  { name: "Engineering", keys: ["github", "linear"] },
  { name: "CRM & Outreach", keys: ["hubspot", "apollo", "phantombuster"] },
  { name: "Docs & Knowledge", keys: ["docs", "notion"] },
  { name: "Operations", keys: ["pylon", "grain", "dataroom"] },
  { name: "Personal", keys: ["google-personal"] },
];

/* ── Skill Categories ──────────────────────────────────────────────── */

export interface SkillCategory {
  name: string;
  color: string;
  bgColor: string;
  skills: string[];
}

export const SKILL_CATEGORIES: SkillCategory[] = [
  {
    name: "Engineering",
    color: "#24292F",
    bgColor: "#F6F8FA",
    skills: ["debugging", "database-reference", "implement-ticket", "github-repos"],
  },
  {
    name: "Support",
    color: "#3B82F6",
    bgColor: "#EFF6FF",
    skills: ["bug-triage", "campaign-visibility", "pylon-support"],
  },
  {
    name: "Product",
    color: "#8B5CF6",
    bgColor: "#F5F3FF",
    skills: ["feature-request-triage", "improve-docs"],
  },
  {
    name: "Sales",
    color: "#F97316",
    bgColor: "#FFF7ED",
    skills: ["presigned-docs-link", "monetize-now", "zoho-books"],
  },
  {
    name: "Customer Success",
    color: "#10B981",
    bgColor: "#ECFDF5",
    skills: ["integration-check"],
  },
  {
    name: "Finance",
    color: "#EAB308",
    bgColor: "#FEFCE8",
    skills: ["account-receivables"],
  },
  {
    name: "Security",
    color: "#EF4444",
    bgColor: "#FEF2F2",
    skills: ["infosec"],
  },
  {
    name: "Self-improvement",
    color: "#A855F7",
    bgColor: "#FAF5FF",
    skills: ["self-improvement"],
  },
];

/* ── Skill → Tool connections ────────────────────────────────────────── */

/** Which tools each skill uses. Empty = no direct tool dependency. */
export const SKILL_TOOL_MAP: Record<string, string[]> = {
  debugging: ["mongodb", "clickhouse", "bigquery", "athena"],
  "database-reference": ["mongodb", "clickhouse", "bigquery", "athena"],
  "implement-ticket": ["github", "linear"],
  "github-repos": ["github"],
  "bug-triage": ["mongodb", "clickhouse", "github"],
  "campaign-visibility": ["mongodb", "clickhouse"],
  "pylon-support": ["pylon"],
  "feature-request-triage": ["linear"],
  "improve-docs": ["docs", "notion"],
  "presigned-docs-link": ["docs"],
  "monetize-now": [],
  "zoho-books": [],
  "integration-check": ["mongodb", "clickhouse"],
  "account-receivables": ["hubspot"],
  infosec: ["docs"],
  "self-improvement": [],
};
