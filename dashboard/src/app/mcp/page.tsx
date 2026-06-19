"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import Link from "next/link";
import { fetchMcpServers, fetchSkills, basePath } from "../../lib/api";
import type { McpServer, Skill } from "../../lib/api";
import { TOOL_LOGOS } from "./tool-logos";
import {
  getToolMeta,
  CATEGORIES,
  SKILL_CATEGORIES,
  SKILL_TOOL_MAP,
} from "./tool-meta";
import CrosscutIcon from "../../components/CrosscutIcon";

/* ── CLI-only tools ───────────────────────────────────────────────── */

const CLI_TOOLS: McpServer[] = [
  { name: "apollo", type: "cli" },
  { name: "phantombuster", type: "cli" },
  { name: "pylon", type: "cli" },
  { name: "grain", type: "cli" },
  { name: "dataroom", type: "cli" },
];

/* ── Types ─────────────────────────────────────────────────────────── */

interface Pt {
  x: number;
  y: number;
}

/* ── Helpers ───────────────────────────────────────────────────────── */

function formatSkillName(name: string) {
  return name
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Bezier path from a -> b, curving horizontally */
function bezier(a: Pt, b: Pt): string {
  const dx = b.x - a.x;
  const cp = Math.abs(dx) * 0.4;
  return `M${a.x},${a.y} C${a.x + cp},${a.y} ${b.x - cp},${b.y} ${b.x},${b.y}`;
}

/** Vertical bezier from a -> b */
function bezierV(a: Pt, b: Pt): string {
  const dy = b.y - a.y;
  const cp = Math.abs(dy) * 0.4;
  return `M${a.x},${a.y} C${a.x},${a.y + cp} ${b.x},${b.y - cp} ${b.x},${b.y}`;
}

/* ── Skill Pill ───────────────────────────────────────────────────── */

function SkillNode({
  name,
  cat,
  isActive,
  onHover,
  nodeRef,
}: {
  name: string;
  cat: (typeof SKILL_CATEGORIES)[number];
  isActive: boolean;
  onHover: (s: string | null) => void;
  nodeRef: (el: HTMLDivElement | null) => void;
}) {
  return (
    <div
      ref={nodeRef}
      className={`group relative inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5
        cursor-pointer select-none transition-all duration-200 whitespace-nowrap
        ${isActive ? "shadow-md scale-[1.03] z-10" : "shadow-sm hover:shadow-md"}`}
      style={{
        borderColor: isActive ? cat.color : `${cat.color}30`,
        backgroundColor: isActive ? cat.bgColor : "white",
      }}
      onMouseEnter={() => onHover(name)}
      onMouseLeave={() => onHover(null)}
    >
      <div
        className="w-2 h-2 rounded-full flex-shrink-0 transition-transform duration-200"
        style={{
          backgroundColor: cat.color,
          transform: isActive ? "scale(1.3)" : undefined,
          boxShadow: isActive ? `0 0 8px ${cat.color}60` : undefined,
        }}
      />
      <Link
        href={`/skills/${name}`}
        className="text-[11px] font-semibold no-underline transition-colors duration-150"
        style={{ color: isActive ? cat.color : "#374151" }}
      >
        {formatSkillName(name)}
      </Link>
    </div>
  );
}

/* ── Tool Card ────────────────────────────────────────────────────── */

function ToolCard({
  name,
  isActive,
  onHover,
  nodeRef,
}: {
  name: string;
  isActive: boolean;
  onHover: (n: string | null) => void;
  nodeRef: (el: HTMLDivElement | null) => void;
}) {
  const meta = getToolMeta(name);
  const Logo = TOOL_LOGOS[name];

  return (
    <div
      ref={nodeRef}
      className={`graph-node relative flex flex-col items-center gap-1.5 rounded-xl border bg-surface
        cursor-pointer select-none w-[88px] py-2.5 px-2 transition-all duration-200
        ${isActive ? "border-brand-300 shadow-lg scale-[1.05]" : "border-gray-100 shadow-sm hover:shadow-md"}`}
      onMouseEnter={() => onHover(name)}
      onMouseLeave={() => onHover(null)}
    >
      <Link href={`/mcp/${name}`} className="absolute inset-0 z-10 rounded-xl" aria-label={`Configure ${meta.displayName}`} />
      <div
        className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{ backgroundColor: isActive ? meta.bgColor : `${meta.bgColor}99` }}
      >
        {Logo ? (
          <Logo className="w-[18px] h-[18px]" />
        ) : (
          <span className="text-xs font-bold" style={{ color: meta.color }}>
            {meta.displayName.charAt(0)}
          </span>
        )}
      </div>
      <span className="text-[10px] font-semibold text-gray-700 text-center leading-tight w-full truncate">
        {meta.displayName}
      </span>
      <span className="text-[8px] px-1.5 py-px rounded-full bg-gray-50 text-gray-400 font-medium whitespace-nowrap">
        {meta.authMethod}
      </span>
    </div>
  );
}

/* ── Short-term Memory Node (Vector DB) ──────────────────────────── */

/** Seeded pseudo-random for consistent dot placement across renders */
function seededRandom(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807 + 0) % 2147483647;
    return s / 2147483647;
  };
}

const VECTOR_DOTS = (() => {
  const rng = seededRandom(42);
  return Array.from({ length: 28 }, (_, i) => ({
    x: 8 + rng() * 84,
    y: 8 + rng() * 84,
    size: 2 + rng() * 3,
    delay: rng() * 4,
    dur: 2 + rng() * 3,
    hue: rng() > 0.5 ? "yellow" : "green",
  }));
})();

const VECTOR_LINES = (() => {
  const rng = seededRandom(99);
  const lines: { x1: number; y1: number; x2: number; y2: number; delay: number }[] = [];
  for (let i = 0; i < 12; i++) {
    lines.push({
      x1: 5 + rng() * 90,
      y1: 5 + rng() * 90,
      x2: 5 + rng() * 90,
      y2: 5 + rng() * 90,
      delay: rng() * 5,
    });
  }
  return lines;
})();

function MemoryNode({ nodeRef }: { nodeRef: React.RefObject<HTMLDivElement | null> }) {
  return (
    <div ref={nodeRef} className="relative w-full group/mem">
      <Link href="/memory" className="absolute inset-0 z-20 rounded-2xl" aria-label="View Short-term Memory" />
      <div className="absolute -inset-4 flex items-center justify-center pointer-events-none">
        <div className="memory-orbit" style={{ animationDuration: "6s" }}>
          <div className="w-2 h-2 rounded-full bg-accent-300/60 shadow-[0_0_6px_rgba(232,255,90,0.4)]" />
        </div>
      </div>
      <div className="absolute -inset-4 flex items-center justify-center pointer-events-none">
        <div className="memory-orbit-reverse" style={{ animationDuration: "8s" }}>
          <div className="w-1.5 h-1.5 rounded-full bg-accent-200/60 shadow-[0_0_6px_rgba(232,255,90,0.3)]" />
        </div>
      </div>
      <div className="absolute -inset-4 flex items-center justify-center pointer-events-none">
        <div className="memory-orbit" style={{ animationDuration: "10s", animationDelay: "2s" }}>
          <div className="w-1 h-1 rounded-full bg-accent-400/40" />
        </div>
      </div>
      <div
        className="memory-node relative rounded-2xl border border-[#c5e04a]
          bg-gradient-to-br from-[#f5ffd6] via-white to-[#fdfff0]
          overflow-hidden transition-all duration-200 group-hover/mem:border-accent-400/60 group-hover/mem:shadow-lg group-hover/mem:shadow-accent-200/30"
        style={{ height: "140px" }}
      >
        <div className="absolute inset-0 pointer-events-none opacity-[0.10]"
          style={{
            backgroundImage: "linear-gradient(rgba(143,184,0,1) 1px, transparent 1px), linear-gradient(90deg, rgba(143,184,0,1) 1px, transparent 1px)",
            backgroundSize: "14px 14px",
          }}
        />
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
          <div className="memory-scan-line absolute inset-x-0 h-[2px] bg-gradient-to-r from-transparent via-accent-400/50 to-transparent" />
        </div>
        <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox="0 0 100 100" preserveAspectRatio="none">
          {VECTOR_LINES.map((l, i) => (
            <line key={`vl-${i}`} x1={`${l.x1}%`} y1={`${l.y1}%`} x2={`${l.x2}%`} y2={`${l.y2}%`}
              stroke="url(#memory-grad-inner)" strokeWidth="0.5" opacity="0.25">
              <animate attributeName="opacity" values="0.1;0.35;0.1" dur={`${3 + l.delay}s`} begin={`${l.delay}s`} repeatCount="indefinite" />
            </line>
          ))}
          <defs>
            <linearGradient id="memory-grad-inner" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#8FB800" />
              <stop offset="100%" stopColor="#c5e04a" />
            </linearGradient>
          </defs>
          {VECTOR_DOTS.map((d, i) => (
            <circle key={`vd-${i}`} cx={`${d.x}%`} cy={`${d.y}%`} r={d.size * 0.4}
              fill={d.hue === "yellow" ? "#c5e04a" : "#8FB800"} opacity="0.5">
              <animate attributeName="opacity" values="0.2;0.55;0.2" dur={`${d.dur}s`} begin={`${d.delay}s`} repeatCount="indefinite" />
              <animate attributeName="r" values={`${d.size * 0.3};${d.size * 0.6};${d.size * 0.3}`} dur={`${d.dur}s`} begin={`${d.delay}s`} repeatCount="indefinite" />
            </circle>
          ))}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 z-10">
          <svg className="w-7 h-7 text-gray-700" fill="none" viewBox="0 0 24 24" strokeWidth={1.2} stroke="currentColor">
            <ellipse cx="12" cy="6" rx="8" ry="3" />
            <path d="M4 6v4c0 1.657 3.582 3 8 3s8-1.343 8-3V6" />
            <path d="M4 10v4c0 1.657 3.582 3 8 3s8-1.343 8-3v-4" />
            <path d="M4 14v4c0 1.657 3.582 3 8 3s8-1.343 8-3v-4" />
          </svg>
          <span className="text-[10px] font-bold text-gray-700 uppercase tracking-widest">
            Short-term Memory
          </span>
          <span className="text-[8px] text-gray-500 font-medium">
            Vector DB &middot; Episodic Recall
          </span>
        </div>
      </div>
    </div>
  );
}

/* ── Skeleton ──────────────────────────────────────────────────────── */

function GraphSkeleton() {
  return (
    <div className="bg-surface border border-gray-200 rounded-2xl overflow-hidden">
      <div className="flex min-h-[780px]">
        <div className="flex flex-col items-center justify-center w-[140px] flex-shrink-0 gap-6">
          <div className="skeleton rounded-full w-[80px] h-[80px]" />
          <div className="skeleton rounded-2xl w-[64px] h-[64px]" />
        </div>
        <div className="flex-[1.3] flex-shrink-0 py-8 px-6 flex flex-col gap-5 justify-center">
          {[6, 4, 5, 5, 4].map((n, i) => (
            <div key={i} className="space-y-2">
              <div className="skeleton h-3 w-20 rounded" />
              <div className="flex flex-wrap gap-2">
                {Array.from({ length: n }).map((_, j) => (
                  <div key={j} className="skeleton rounded-full h-[28px] w-[100px]" />
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="flex-1 py-8 pr-6 flex flex-col gap-4 justify-center">
          {[3, 2, 3, 3, 3].map((n, i) => (
            <div key={i} className="flex gap-2">
              {Array.from({ length: n }).map((_, j) => (
                <div key={j} className="skeleton rounded-xl w-[88px] h-[76px]" />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Skill Group Renderer ─────────────────────────────────────────── */

function SkillGroup({
  categories,
  activeSkills,
  handleSkillHover,
  skillRefs,
  catHeaderRefs,
}: {
  categories: typeof SKILL_CATEGORIES;
  activeSkills: Set<string>;
  handleSkillHover: (s: string | null) => void;
  skillRefs: React.MutableRefObject<Record<string, HTMLDivElement | null>>;
  catHeaderRefs: React.MutableRefObject<Record<string, HTMLDivElement | null>>;
}) {
  return (
    <div className="flex flex-col gap-5 md:gap-6">
      {categories.map((cat) => (
        <div key={cat.name}>
          <div
            ref={(el) => { catHeaderRefs.current[cat.name] = el; }}
            className="flex items-center gap-1.5 mb-2"
          >
            <div
              className="w-2.5 h-2.5 rounded-full"
              style={{
                backgroundColor: cat.color,
                boxShadow: `0 0 6px ${cat.color}30`,
              }}
            />
            <span
              className="text-[10px] font-bold uppercase tracking-wider"
              style={{ color: cat.color }}
            >
              {cat.name}
            </span>
            <span className="text-[9px] text-gray-400 font-medium">{cat.skills.length}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {cat.skills.map((name) => (
              <SkillNode
                key={name}
                name={name}
                cat={cat}
                isActive={activeSkills.has(name)}
                onHover={handleSkillHover}
                nodeRef={(el) => { skillRefs.current[name] = el; }}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Page ──────────────────────────────────────────────────────────── */

export default function GraphPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [hoveredSkill, setHoveredSkill] = useState<string | null>(null);
  const [hoveredTool, setHoveredTool] = useState<string | null>(null);

  const [positions, setPositions] = useState<{
    loma: Pt;
    memory: Pt;
    skills: Record<string, Pt>;
    tools: Record<string, Pt>;
  } | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const lomaRef = useRef<HTMLDivElement>(null);
  const memoryRef = useRef<HTMLDivElement>(null);
  const skillRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const toolRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const catHeaderRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    Promise.all([
      fetchMcpServers()
        .then((d) => {
          const names = new Set(d.servers.map((s) => s.name));
          return [...d.servers, ...CLI_TOOLS.filter((t) => !names.has(t.name))];
        })
        .catch(() => CLI_TOOLS as McpServer[]),
      fetchSkills()
        .then((d) => d.skills)
        .catch(() => [] as Skill[]),
    ]).then(([srv, sk]) => {
      setServers(srv);
      setSkills(sk);
      setLoading(false);
    });
  }, []);

  const measure = useCallback(() => {
    const box = containerRef.current;
    const loma = lomaRef.current;
    const mem = memoryRef.current;
    if (!box || !loma) return;

    const cr = box.getBoundingClientRect();
    const gr = loma.getBoundingClientRect();

    const lomaPos: Pt = {
      x: gr.left + gr.width / 2 - cr.left,
      y: gr.top + gr.height / 2 - cr.top,
    };

    let memoryPos: Pt = { x: lomaPos.x, y: lomaPos.y + 120 };
    if (mem) {
      const mr = mem.getBoundingClientRect();
      memoryPos = {
        x: mr.left + mr.width / 2 - cr.left,
        y: mr.top + mr.height / 2 - cr.top,
      };
    }

    const skillPos: Record<string, Pt> = {};
    Object.entries(skillRefs.current).forEach(([name, el]) => {
      if (!el) return;
      const r = el.getBoundingClientRect();
      skillPos[name] = {
        x: r.left + r.width / 2 - cr.left,
        y: r.top + r.height / 2 - cr.top,
      };
    });

    const toolPos: Record<string, Pt> = {};
    Object.entries(toolRefs.current).forEach(([name, el]) => {
      if (!el) return;
      const r = el.getBoundingClientRect();
      toolPos[name] = {
        x: r.left + r.width / 2 - cr.left,
        y: r.top + r.height / 2 - cr.top,
      };
    });

    setPositions({ loma: lomaPos, memory: memoryPos, skills: skillPos, tools: toolPos });
  }, []);

  useEffect(() => {
    if (loading) return;
    const t = setTimeout(measure, 100);
    const obs = new ResizeObserver(measure);
    if (containerRef.current) obs.observe(containerRef.current);
    return () => {
      clearTimeout(t);
      obs.disconnect();
    };
  }, [loading, measure]);

  const activeSkills = new Set<string>();
  const activeTools = new Set<string>();

  if (hoveredSkill) {
    activeSkills.add(hoveredSkill);
    (SKILL_TOOL_MAP[hoveredSkill] || []).forEach((t) => activeTools.add(t));
  }
  if (hoveredTool) {
    activeTools.add(hoveredTool);
    Object.entries(SKILL_TOOL_MAP).forEach(([skill, tools]) => {
      if (tools.includes(hoveredTool)) activeSkills.add(skill);
    });
  }

  const hasHover = hoveredSkill !== null || hoveredTool !== null;

  const handleSkillHover = useCallback((s: string | null) => setHoveredSkill(s), []);
  const handleToolHover = useCallback((t: string | null) => setHoveredTool(t), []);

  const totalSkills = SKILL_CATEGORIES.reduce((n, c) => n + c.skills.length, 0);
  const totalTools = CATEGORIES.reduce((n, c) => n + c.keys.length, 0);

  if (loading) {
    return (
      <div className="space-y-4 md:space-y-6 animate-fade-in-up">
        <div>
          <h1 className="text-lg md:text-xl font-semibold text-gray-900">Context Graph</h1>
          <p className="text-sm text-gray-500 mt-1">Loading graph...</p>
        </div>
        <GraphSkeleton />
      </div>
    );
  }

  return (
    <div className="space-y-4 md:space-y-6 animate-fade-in-up">
      <div>
        <h1 className="text-lg md:text-xl font-semibold text-gray-900">Context Graph</h1>
        <p className="text-sm text-gray-500 mt-1">
          Long-term memory, episodic recall, and motor execution.
        </p>
      </div>

      <div className="bg-surface border border-gray-200 rounded-2xl overflow-hidden">
        <div ref={containerRef} className="relative">
          {/* ── Top: Short-term Memory ── */}
          <div className="border-b border-accent-200/20 px-6 md:px-8 py-5 md:py-6">
            <div className="max-w-[480px] mx-auto">
              <MemoryNode nodeRef={memoryRef} />
            </div>
          </div>

          {/* ── SVG connections ── */}
          {positions && (
            <svg className="absolute inset-0 w-full h-full pointer-events-none z-0">
              <defs>
                <linearGradient id="glow-grad" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#1F1D1A" />
                  <stop offset="100%" stopColor="#E8FF5A" />
                </linearGradient>
                <linearGradient id="memory-grad" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stopColor="#c5e04a" />
                  <stop offset="100%" stopColor="#E8FF5A" />
                </linearGradient>
                <linearGradient id="sync-grad" x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stopColor="#7A8F00" stopOpacity="0.8" />
                  <stop offset="100%" stopColor="#8FB800" stopOpacity="0.3" />
                </linearGradient>
              </defs>

              {SKILL_CATEGORIES.flatMap((cat) => cat.skills).map((skillName, si) => {
                const skillEl = skillRefs.current[skillName];
                if (!skillEl || !containerRef.current) return null;
                const cr = containerRef.current.getBoundingClientRect();
                const sr = skillEl.getBoundingClientRect();
                const skillPt: Pt = { x: sr.left + sr.width / 2 - cr.left, y: sr.top - cr.top };
                const memBottom: Pt = { x: positions.memory.x, y: positions.memory.y + 70 };
                const d = bezierV(memBottom, skillPt);
                return (
                  <g key={`sync-${skillName}`}>
                    <path d={d} fill="none" stroke="url(#sync-grad)" strokeWidth={1}
                      strokeDasharray="4 4" opacity={0.5} />
                    <circle r="1.5" fill="#8FB800" opacity="0.5">
                      <animateMotion dur={`${3.5 + si * 0.4}s`} repeatCount="indefinite" path={d} />
                    </circle>
                  </g>
                );
              })}

              {Object.entries(positions.skills).map(([name, sp]) => {
                const on = activeSkills.has(name);
                const faded = hasHover && !on;
                const lomaRight: Pt = { x: positions.loma.x + 44, y: positions.loma.y };
                const skillLeft: Pt = { x: sp.x - 50, y: sp.y };
                const d = bezier(lomaRight, skillLeft);
                return (
                  <g key={`g-${name}`}>
                    <path d={d} fill="none"
                      stroke={on ? "url(#glow-grad)" : "#e5e7eb"}
                      strokeWidth={on ? 2 : 0.7}
                      opacity={faded ? 0.06 : on ? 0.75 : 0.12}
                      className="transition-all duration-300" />
                    {on && (
                      <circle r="2" fill="url(#glow-grad)" opacity="0.6">
                        <animateMotion dur="1.5s" repeatCount="indefinite" path={d} />
                      </circle>
                    )}
                  </g>
                );
              })}

              {Object.entries(SKILL_TOOL_MAP).map(([skill, tools]) =>
                tools.map((tool) => {
                  const sp = positions.skills[skill];
                  const tp = positions.tools[tool];
                  if (!sp || !tp) return null;
                  const on = activeSkills.has(skill) && activeTools.has(tool);
                  const faded = hasHover && !on;
                  const skillRight: Pt = { x: sp.x + 50, y: sp.y };
                  const toolLeft: Pt = { x: tp.x - 44, y: tp.y };
                  const d = bezier(skillRight, toolLeft);
                  return (
                    <g key={`${skill}-${tool}`}>
                      <path d={d} fill="none"
                        stroke={on ? "url(#glow-grad)" : "#e5e7eb"}
                        strokeWidth={on ? 2 : 0.5}
                        opacity={faded ? 0.03 : on ? 0.8 : 0.08}
                        className="transition-all duration-300" />
                      {on && (
                        <circle r="2.5" fill="url(#glow-grad)" opacity="0.7">
                          <animateMotion dur="2s" repeatCount="indefinite" path={d} />
                        </circle>
                      )}
                    </g>
                  );
                }),
              )}
            </svg>
          )}

          {/* ── Bottom: 3-column flow ── */}
          <div className="flex min-h-[680px]">
            {/* ── Col 1: Agent ── */}
            <div className="flex flex-col items-center justify-center w-[130px] md:w-[160px] flex-shrink-0 relative z-10">
              <div className="relative">
                <div
                  className="loma-ring w-[110px] h-[110px] md:w-[130px] md:h-[130px]"
                  style={{ border: "1.5px solid rgba(232, 255, 90, 0.3)" }}
                />
                <div
                  className="loma-ring-delayed w-[110px] h-[110px] md:w-[130px] md:h-[130px]"
                  style={{ border: "1.5px solid rgba(197, 224, 74, 0.2)" }}
                />
                <div
                  ref={lomaRef}
                  className="relative center-node bg-gradient-to-br from-[#D4FF00] via-[#E8FF5A] to-[#F0FF85]
                             border-2 border-[#c5e04a] rounded-full
                             w-[80px] h-[80px] md:w-[92px] md:h-[92px]
                             flex flex-col items-center justify-center gap-0.5"
                >
                  <CrosscutIcon size={28} />
                  <span className="font-[family-name:var(--font-logo)] text-sm font-black tracking-[1px] text-gray-900">
                    Loma
                  </span>
                </div>
              </div>
            </div>

            {/* ── Col 2: Long-term Memory (Skills) ── */}
            <div className="flex-1 min-w-0 py-6 md:py-8 px-4 md:px-6 flex flex-col items-center gap-5 md:gap-6 justify-center relative z-10">
              <SkillGroup
                categories={SKILL_CATEGORIES}
                activeSkills={activeSkills}
                handleSkillHover={handleSkillHover}
                skillRefs={skillRefs}
                catHeaderRefs={catHeaderRefs}
              />
            </div>

            {/* ── Col 3: Motor System (Tools) ── */}
            <div className="flex-1 min-w-0 py-6 md:py-8 px-5 md:px-6 flex flex-col gap-5 md:gap-6 justify-center relative z-10">
              {CATEGORIES.map((cat) => (
                <div key={cat.name}>
                  <div className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-2 ml-1">
                    {cat.name}
                  </div>
                  <div className="flex gap-2 flex-wrap">
                    {cat.keys.map((key) => (
                      <ToolCard
                        key={key}
                        name={key}
                        isActive={activeTools.has(key)}
                        onHover={handleToolHover}
                        nodeRef={(el) => { toolRefs.current[key] = el; }}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Legend ── */}
        <div className="border-t border-gray-200 flex bg-gray-50/60">
          <div className="w-[130px] md:w-[160px] flex-shrink-0 text-center py-3">
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Agent</span>
          </div>
          <div className="flex-1 text-center py-3 border-x border-gray-200">
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Long-term Memory</span>
            <span className="text-[10px] text-gray-400 ml-1.5">{totalSkills} skills</span>
          </div>
          <div className="flex-1 text-center py-3">
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Motor System</span>
            <span className="text-[10px] text-gray-400 ml-1.5">{totalTools} tools</span>
          </div>
        </div>
      </div>
    </div>
  );
}
