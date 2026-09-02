"use client";

import type { AgentAvatarSpec, AgentMotif } from "@/lib/agents-api";

/**
 * Deterministic generative avatar — an abstract head-and-shoulders "being"
 * rendered from the agent's stored seed + motif, in the Loma palette (warm
 * neutral ramp + lime accent via CSS variables, so light/dark both work).
 * No image pipeline: the same spec always renders the same headshot.
 */

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const BG_TONES = [
  "var(--color-brand-100, #F7F3EA)",
  "var(--color-brand-200, #F0EADC)",
  "var(--color-accent-200, #E8FF5A)",
];
const INK = "var(--color-brand-800, #2A2723)";
const ACCENT = "var(--color-accent-200, #E8FF5A)";

export function randomAvatarSpec(motif?: AgentMotif): AgentAvatarSpec {
  const motifs: AgentMotif[] = ["round", "square", "halo", "antenna"];
  return {
    seed: Math.floor(Math.random() * 2 ** 31),
    motif: motif ?? motifs[Math.floor(Math.random() * motifs.length)],
  };
}

export function AgentAvatar({
  avatar,
  size = 24,
  className,
}: {
  avatar?: AgentAvatarSpec | null;
  size?: number;
  className?: string;
}) {
  const seed = avatar?.seed ?? 1;
  const motif = avatar?.motif ?? "round";
  const rand = mulberry32(seed);

  const bg = BG_TONES[Math.floor(rand() * BG_TONES.length)];
  // On a lime background the being reads best in ink-on-accent; elsewhere the
  // accent shows up as the halo/antenna/collar detail.
  const onAccentBg = bg === ACCENT;
  const detail = onAccentBg ? INK : ACCENT;

  const headRx = motif === "square" ? 5 : 11;
  const headW = 20 + Math.floor(rand() * 4); // 20–23
  const headX = 32 - headW / 2;
  const headY = motif === "halo" || motif === "antenna" ? 18 : 14;
  const eyeGap = 5 + Math.floor(rand() * 3); // 5–7
  const eyeY = headY + 10 + Math.floor(rand() * 3);
  const shoulderW = 34 + Math.floor(rand() * 8); // 34–41
  const collar = rand() > 0.5 && !onAccentBg;

  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={className}
      aria-hidden
      style={{ borderRadius: "9999px", flexShrink: 0 }}
    >
      <rect width="64" height="64" fill={bg} />
      {motif === "halo" && (
        <path
          d={`M ${32 - 13} 12 A 13 13 0 0 1 ${32 + 13} 12`}
          fill="none"
          stroke={detail}
          strokeWidth="3"
          strokeLinecap="round"
        />
      )}
      {motif === "antenna" && (
        <>
          <line x1="32" y1="18" x2="32" y2="10" stroke={INK} strokeWidth="2.5" />
          <circle cx="32" cy="8" r="3" fill={detail} stroke={onAccentBg ? "none" : INK} strokeWidth={onAccentBg ? 0 : 1} />
        </>
      )}
      {/* head */}
      <rect x={headX} y={headY} width={headW} height={headW} rx={headRx} fill={INK} />
      {/* eyes — status-as-dots, the Loma motif */}
      <circle cx={32 - eyeGap / 2 - 1} cy={eyeY} r="2" fill={bg} />
      <circle cx={32 + eyeGap / 2 + 1} cy={eyeY} r="2" fill={bg} />
      {/* shoulders */}
      <rect x={32 - shoulderW / 2} y={headY + headW + 4} width={shoulderW} height="24" rx="10" fill={INK} />
      {collar && (
        <rect x={32 - 5} y={headY + headW + 4} width="10" height="5" rx="2.5" fill={detail} />
      )}
    </svg>
  );
}
