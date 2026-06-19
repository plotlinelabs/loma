"use client";

import {
  FEATURED_SUGGESTED,
  MORE_SUGGESTIONS,
  SUGGESTED_BANNER,
  type SuggestedStep,
  type MoreSuggestion,
} from "./suggested-data";
import { TOOL_LOGOS } from "../mcp/tool-logos";

/* ── Design tokens ─────────────────────────────────────────── */
const BG = "#FDFBF7";
const SURFACE = "#FFFFFF";
const SURFACE_ALT = "#FAF8F3";
const BORDER = "#E8E4DD";
const TEXT_PRIMARY = "#1F1D1A";
const TEXT_SECONDARY = "#5C5650";
const TEXT_MUTED = "#8C857D";
const LIME = "#C8E84A";
const LIME_TINT = "#FBFEEC";
const LIME_DEEP = "#5B7A0E";

/* Maps suggested-flow step tool keys to the TOOL_LOGOS map */
const TOOL_KEY_MAP: Record<string, string> = {
  pylon: "pylon",
  linear: "linear",
  github: "github",
  slack: "slack",
  notion: "notion",
  clickhouse: "clickhouse",
  mongodb: "mongodb",
  grain: "grain",
  hubspot: "hubspot",
  bamboohr: "bamboohr",
};

function StepIcon({ tool }: { tool: SuggestedStep["tool"] }) {
  const key = TOOL_KEY_MAP[tool] || tool;
  const Logo = TOOL_LOGOS[key];
  return (
    <span
      className="inline-flex items-center justify-center flex-shrink-0 rounded-[6px]"
      style={{ width: 22, height: 22, background: SURFACE, border: `1px solid ${BORDER}` }}
      title={tool}
    >
      {Logo ? <Logo className="w-3 h-3" /> : <span style={{ width: 8, height: 8, borderRadius: "50%", background: TEXT_MUTED }} />}
    </span>
  );
}

function MiniToolDot({ tool }: { tool: MoreSuggestion["tools"][number] }) {
  const Logo = TOOL_LOGOS[tool];
  return (
    <span
      className="inline-flex items-center justify-center flex-shrink-0 rounded-[4px]"
      style={{ width: 16, height: 16, background: SURFACE, border: `1px solid ${BORDER}` }}
    >
      {Logo ? <Logo className="w-2.5 h-2.5" /> : <span style={{ width: 6, height: 6, borderRadius: "50%", background: TEXT_MUTED }} />}
    </span>
  );
}

function KindPill({ kind }: { kind: "event" | "schedule" }) {
  const label = kind === "event" ? "Event-triggered" : "Schedule";
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-[6px]"
      style={{
        background: SURFACE_ALT,
        border: `1px solid ${BORDER}`,
        fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
        fontSize: 10,
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        color: TEXT_SECONDARY,
        fontWeight: 600,
      }}
    >
      {kind === "event" ? (
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M5.5 1L1.5 6h3l-1 3L8 4H5l0.5-3z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" fill="none" />
        </svg>
      ) : (
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <circle cx="5" cy="5" r="4" stroke="currentColor" strokeWidth="1.2" />
          <path d="M5 2.5v3l2 1" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      )}
      {label}
    </span>
  );
}

function SuggestedFlowPill() {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-[6px]"
      style={{
        background: LIME_TINT,
        border: `1px solid ${LIME}`,
        fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
        fontSize: 10,
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        color: LIME_DEEP,
        fontWeight: 600,
      }}
    >
      + Suggested flow
    </span>
  );
}

function StatBox({
  label,
  value,
  sub,
  highlighted,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  highlighted?: boolean;
}) {
  return (
    <div
      className="rounded-[10px] p-3"
      style={{
        background: highlighted ? LIME_TINT : SURFACE_ALT,
        border: highlighted ? `1.5px solid ${LIME}` : `1px solid ${BORDER}`,
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: TEXT_MUTED,
          fontWeight: 500,
        }}
      >
        {label}
      </div>
      <div
        className="mt-1"
        style={{
          fontFamily: "var(--font-outfit), system-ui, sans-serif",
          fontSize: 22,
          fontWeight: 700,
          color: TEXT_PRIMARY,
          letterSpacing: "-0.01em",
          lineHeight: "26px",
        }}
      >
        {value}
      </div>
      {sub && (
        <div
          className="mt-0.5"
          style={{
            fontFamily: "var(--font-figtree), system-ui, sans-serif",
            fontSize: 11,
            lineHeight: "14px",
            color: TEXT_MUTED,
          }}
        >
          {sub}
        </div>
      )}
    </div>
  );
}

function ConfidenceDots({ count }: { count: number }) {
  return (
    <div className="flex items-center gap-0.5 mt-1.5">
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: i < count ? LIME : "#E8E4DD",
            border: `1px solid ${i < count ? TEXT_PRIMARY : "#D8D3CA"}`,
          }}
        />
      ))}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
        fontSize: 10,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: TEXT_MUTED,
        fontWeight: 500,
      }}
    >
      {children}
    </div>
  );
}

/* ── Featured flow card ───────────────────────────────────── */

function FeaturedFlowCard() {
  const f = FEATURED_SUGGESTED;
  return (
    <div
      className="rounded-[14px] p-6"
      style={{ background: SURFACE, border: `1px solid ${BORDER}` }}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <SuggestedFlowPill />
          <KindPill kind={f.triggerKind} />
        </div>
        <span
          style={{
            fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
            fontSize: 11,
            color: TEXT_MUTED,
          }}
        >
          Detected {f.detectedAgo}
        </span>
      </div>

      {/* Title + description */}
      <h2
        className="mt-3"
        style={{
          fontFamily: "var(--font-outfit), system-ui, sans-serif",
          fontSize: 26,
          lineHeight: "32px",
          fontWeight: 700,
          letterSpacing: "-0.02em",
          color: TEXT_PRIMARY,
        }}
      >
        {f.name}
      </h2>
      <p
        className="mt-2 max-w-[820px]"
        style={{
          fontFamily: "var(--font-figtree), system-ui, sans-serif",
          fontSize: 14,
          lineHeight: "21px",
          color: TEXT_SECONDARY,
        }}
      >
        {f.description}
      </p>

      {/* Stats row */}
      <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <StatBox label="Pattern observed" value={f.patternCount} sub={f.patternWindow} />
        <StatBox label="Avg time per run" value={f.avgTime} sub={f.avgTimeSub} />
        <StatBox label="Time you'd save" value={f.timeSaved} sub={f.timeSavedSub} highlighted />
        <StatBox
          label="Confidence"
          value=""
          sub={
            <div className="flex flex-col">
              <ConfidenceDots count={f.confidenceDots} />
              <span className="mt-1">{f.confidenceLabel}</span>
            </div>
          }
        />
      </div>

      {/* Proposed flow */}
      <div className="mt-6">
        <SectionLabel>Proposed flow</SectionLabel>

        <div className="mt-2 rounded-[12px] overflow-hidden" style={{ border: `1px solid ${BORDER}` }}>
          {/* Trigger bar */}
          <div
            className="flex items-center justify-between gap-3 px-4 py-3"
            style={{ background: TEXT_PRIMARY, color: "#FFFFFF" }}
          >
            <div className="flex items-center gap-3 min-w-0">
              <span
                className="inline-flex items-center justify-center rounded-[6px] flex-shrink-0"
                style={{ width: 26, height: 26, background: "rgba(255,255,255,0.08)" }}
              >
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <path
                    d="M6.5 1.5l1.4 2.8 3.1 0.5-2.2 2.2 0.5 3.1-2.8-1.5-2.8 1.5 0.5-3.1-2.2-2.2 3.1-0.5z"
                    stroke={LIME}
                    strokeWidth="1.2"
                    strokeLinejoin="round"
                    fill="none"
                  />
                </svg>
              </span>
              <div className="min-w-0">
                <div
                  style={{
                    fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
                    fontSize: 10,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    color: "rgba(255,255,255,0.6)",
                  }}
                >
                  Trigger
                </div>
                <div
                  className="mt-0.5"
                  style={{
                    fontFamily: "var(--font-figtree), system-ui, sans-serif",
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  {f.triggerDescription}
                </div>
              </div>
            </div>
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 flex-shrink-0"
              style={{ background: "rgba(255,255,255,0.12)" }}
            >
              <StepIcon tool="pylon" />
              <span
                style={{
                  fontFamily: "var(--font-figtree), system-ui, sans-serif",
                  fontSize: 11,
                  fontWeight: 500,
                  color: "#FFFFFF",
                }}
              >
                {f.triggerSource}
              </span>
            </span>
          </div>

          {/* Steps */}
          <div className="flex flex-col" style={{ background: SURFACE }}>
            {f.steps.map((step, i) => (
              <div
                key={i}
                className="flex items-start gap-3 px-4 py-2.5"
                style={{ borderTop: i === 0 ? "none" : `1px solid ${BORDER}` }}
              >
                <span
                  className="inline-flex items-center justify-center flex-shrink-0 rounded-full mt-0.5"
                  style={{
                    width: 20,
                    height: 20,
                    background: SURFACE_ALT,
                    border: `1px solid ${BORDER}`,
                    fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
                    fontSize: 10,
                    fontWeight: 600,
                    color: TEXT_SECONDARY,
                  }}
                >
                  {i + 1}
                </span>
                <StepIcon tool={step.tool} />
                <span
                  className="flex-1 min-w-0"
                  style={{
                    fontFamily: "var(--font-figtree), system-ui, sans-serif",
                    fontSize: 13,
                    lineHeight: "20px",
                    color: TEXT_PRIMARY,
                    wordBreak: "break-word",
                  }}
                >
                  {step.description}
                </span>
                {step.learned && (
                  <span
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[4px] flex-shrink-0 mt-1"
                    style={{
                      background: LIME_TINT,
                      border: `1px solid ${LIME}`,
                      fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
                      fontSize: 9,
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      color: LIME_DEEP,
                      fontWeight: 600,
                    }}
                  >
                    learned
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Learned from these work threads */}
      <div className="mt-6">
        <SectionLabel>Learned from these work threads</SectionLabel>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {f.learnedFromThreads.map((t) => (
            <span
              key={t.title}
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1"
              style={{
                background: SURFACE_ALT,
                border: `1px solid ${BORDER}`,
              }}
            >
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                <circle cx="5" cy="5" r="4" stroke={TEXT_MUTED} strokeWidth="1.2" />
                <path d="M3.5 5l1.2 1.2L7 4" stroke={TEXT_MUTED} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span
                style={{
                  fontFamily: "var(--font-figtree), system-ui, sans-serif",
                  fontSize: 11,
                  color: TEXT_PRIMARY,
                  fontWeight: 500,
                }}
              >
                {t.title}
              </span>
              <span
                style={{
                  fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
                  fontSize: 9,
                  color: TEXT_MUTED,
                }}
              >
                {t.relativeTime}
              </span>
            </span>
          ))}
          <span
            className="inline-flex items-center gap-1 rounded-full px-2.5 py-1"
            style={{
              background: SURFACE,
              border: `1px dashed ${BORDER}`,
              fontFamily: "var(--font-figtree), system-ui, sans-serif",
              fontSize: 11,
              color: TEXT_MUTED,
              fontWeight: 500,
            }}
          >
            + 3 more
          </span>
        </div>
      </div>

      {/* Actions */}
      <div className="mt-6 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <button
            className="inline-flex items-center gap-1.5 rounded-[10px] px-4 py-2.5 press-scale"
            style={{
              background: TEXT_PRIMARY,
              fontFamily: "var(--font-figtree), system-ui, sans-serif",
              fontSize: 13,
              fontWeight: 600,
              color: "#FFFFFF",
            }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M2 6.2L5 9L10 3.5" stroke={LIME} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Accept &amp; turn on flow
          </button>
          <button
            className="inline-flex items-center gap-1.5 rounded-[10px] px-4 py-2.5"
            style={{
              background: SURFACE,
              border: `1px solid ${BORDER}`,
              fontFamily: "var(--font-figtree), system-ui, sans-serif",
              fontSize: 13,
              fontWeight: 500,
              color: TEXT_PRIMARY,
            }}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path
                d="M8 2l2 2-6 6H2v-2z"
                stroke={TEXT_PRIMARY}
                strokeWidth="1.4"
                strokeLinejoin="round"
                fill="none"
              />
            </svg>
            Edit before accepting
          </button>
        </div>
        <div className="flex items-center gap-3">
          <button
            style={{
              fontFamily: "var(--font-figtree), system-ui, sans-serif",
              fontSize: 12,
              color: TEXT_MUTED,
            }}
          >
            Not useful
          </button>
          <button
            style={{
              fontFamily: "var(--font-figtree), system-ui, sans-serif",
              fontSize: 12,
              color: TEXT_MUTED,
            }}
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── More suggestions ─────────────────────────────────────── */

function MoreSuggestionCard({ suggestion }: { suggestion: MoreSuggestion }) {
  const authorColors = ["#6B4E9B", "#1F4FA8", "#0D8F4E", "#B8860B", "#B23A48"];
  return (
    <div
      className="rounded-[12px] p-4 flex items-start gap-3"
      style={{ background: SURFACE, border: `1px solid ${BORDER}` }}
    >
      <span
        className="inline-flex items-center justify-center flex-shrink-0 rounded-[8px]"
        style={{ width: 32, height: 32, background: SURFACE_ALT, border: `1px solid ${BORDER}` }}
      >
        {suggestion.triggerKind === "schedule" ? (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <circle cx="7" cy="7" r="5.5" stroke={TEXT_SECONDARY} strokeWidth="1.3" />
            <path d="M7 4v3l2 1" stroke={TEXT_SECONDARY} strokeWidth="1.3" strokeLinecap="round" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M8 1L2 8h4l-1 5L12 6H7l1-5z" stroke={TEXT_SECONDARY} strokeWidth="1.3" strokeLinejoin="round" fill="none" />
          </svg>
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <h3
            style={{
              fontFamily: "var(--font-outfit), system-ui, sans-serif",
              fontSize: 15,
              fontWeight: 700,
              color: TEXT_PRIMARY,
              letterSpacing: "-0.01em",
            }}
          >
            {suggestion.name}
          </h3>
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[4px]"
            style={{
              background: SURFACE_ALT,
              border: `1px solid ${BORDER}`,
              fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
              fontSize: 9,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: TEXT_SECONDARY,
              fontWeight: 600,
            }}
          >
            {suggestion.triggerKind === "schedule" ? "Schedule" : "Event"}
          </span>
        </div>
        <p
          className="mt-1.5"
          style={{
            fontFamily: "var(--font-figtree), system-ui, sans-serif",
            fontSize: 12,
            lineHeight: "17px",
            color: TEXT_SECONDARY,
          }}
        >
          {suggestion.description}
        </p>
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <div className="flex items-center -space-x-1.5">
            {suggestion.authors.map((initial, i) => (
              <span
                key={i}
                className="inline-flex items-center justify-center rounded-full"
                style={{
                  width: 18,
                  height: 18,
                  background: authorColors[i % authorColors.length],
                  color: "#FFFFFF",
                  fontFamily: "var(--font-figtree), system-ui, sans-serif",
                  fontSize: 9,
                  fontWeight: 600,
                  border: `1.5px solid ${SURFACE}`,
                }}
              >
                {initial}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-1">
            {suggestion.tools.map((tool) => (
              <MiniToolDot key={tool} tool={tool} />
            ))}
          </div>
          <span
            style={{
              fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
              fontSize: 10,
              color: TEXT_MUTED,
            }}
          >
            {suggestion.ranSummary}
          </span>
          <span style={{ color: "#C8C4BD" }}>·</span>
          <span
            style={{
              fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
              fontSize: 10,
              color: TEXT_MUTED,
            }}
          >
            {suggestion.timeSaved}
          </span>
        </div>
      </div>
      <button
        className="rounded-[8px] px-3 py-1.5 flex-shrink-0"
        style={{
          background: TEXT_PRIMARY,
          fontFamily: "var(--font-figtree), system-ui, sans-serif",
          fontSize: 12,
          fontWeight: 600,
          color: "#FFFFFF",
        }}
      >
        Review
      </button>
    </div>
  );
}

/* ── Exported tab ─────────────────────────────────────────── */

export default function SuggestedFlowsTab() {
  return (
    <div className="space-y-5" style={{ background: BG }}>
      {/* Info banner */}
      <div
        className="rounded-[12px] px-4 py-3 flex items-center justify-between gap-3 flex-wrap"
        style={{ background: LIME_TINT, border: `1px solid ${LIME}` }}
      >
        <div className="flex items-start gap-2.5 min-w-0">
          <span
            className="inline-flex items-center justify-center rounded-full flex-shrink-0 mt-0.5"
            style={{ width: 16, height: 16, background: LIME, border: `1px solid ${TEXT_PRIMARY}` }}
          >
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
              <path d="M4 1v6M1 4h6" stroke={TEXT_PRIMARY} strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </span>
          <span
            style={{
              fontFamily: "var(--font-figtree), system-ui, sans-serif",
              fontSize: 13,
              lineHeight: "19px",
              color: TEXT_PRIMARY,
            }}
          >
            {SUGGESTED_BANNER.message}
          </span>
        </div>
        <button
          style={{
            fontFamily: "var(--font-figtree), system-ui, sans-serif",
            fontSize: 12,
            fontWeight: 600,
            color: TEXT_PRIMARY,
            textDecoration: "underline",
          }}
        >
          {SUGGESTED_BANNER.link}
        </button>
      </div>

      {/* Featured flow card */}
      <FeaturedFlowCard />

      {/* More suggestions */}
      <div className="mt-8">
        <div
          className="mb-3"
          style={{
            fontFamily: "var(--font-jetbrains), ui-monospace, monospace",
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: TEXT_MUTED,
            fontWeight: 500,
          }}
        >
          More suggestions
        </div>
        <div className="flex flex-col gap-3">
          {MORE_SUGGESTIONS.map((s) => (
            <MoreSuggestionCard key={s.id} suggestion={s} />
          ))}
        </div>
      </div>
    </div>
  );
}
