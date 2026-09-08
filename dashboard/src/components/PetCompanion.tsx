"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useUser } from "@/lib/UserContext";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";


// Original, code-native pixel sprites. Shared silhouettes keep every size crisp.
const silhouettes = {
  cat: ["................", "...oo......oo...", "...oao....oao...", "...oaaooooaao...", "...oaaaaaaaao...", "..oaaaaaaaaaao..", "..oaaeaaaeaaao..", "..oaaaanaaaaao..", "...oaammaaoo....", "....oammaao..oo.", "....oammaao..oa.", "...oaaaaaaao.oa.", "...oaaaaaaaooa..", "...oaooooaaoo...", "....oo...oo.....", "................"],
  dog: ["................", "................", "...ooo....ooo...", "..oaaaooooaaao..", "..oaabbbbbaaao..", "..oaabbbbbaaao..", "..oaaebbbeaaao..", "...oabbnbbao....", "....obmmmbbo....", "....obmmmbbo.oo.", "....obmmmbbo.oa.", "...obbbbbbbooao.", "...obbbbbbbooo..", "...oboooobbo....", "....oo...oo.....", "................"],
  rabbit: ["....oo..oo......", "...oaaooaao.....", "...opaaoapo.....", "...opaaoapo.....", "...oaaooaao.....", "....oaaaao......", "...oaaaaaao.....", "...oeaaaeao.....", "...oaanaaaao....", "....oammaao.....", "...oaammaaao....", "..oaaaaaaaaaooo.", "..oaaaaaaaaaoao.", "...ooaoooaoooo..", "....oo...oo.....", "................"],
  hamster: ["................", "................", "...ooo....ooo...", "..oapaoooopaoo..", "..oaaaaaaaaaao..", "..oaaaaaaaaaao..", "..oaaeaaaeaaao..", ".oapppanapppaao.", ".oaaammmmmaaaao.", "..oaammmmmaao...", "..oaammmmmaao...", "..oaaaaaaaaao...", "...oaaaaaaao....", "...oaooooaao....", "....oo...oo.....", "................"],
  parrot: ["................", "......ooo.......", ".....oaaao......", "....oaaaaao.....", "....oaaeeao.....", "....oaammmmo....", "....oaammo......", "...oaaaaao......", "..oabbaaaao.....", "..oabbbaaao.....", "..oabbbaaao.....", "...obbaaao......", "....oaaaao......", "....omoomoo.....", "...oooooooooo...", "................"],
};

export const PETS = [
  { id: "tabby", name: "Tabby cat", kind: "cat", coat: "#d98c45", patch: "#98562e" },
  { id: "tuxedo", name: "Tuxedo cat", kind: "cat", coat: "#454754", patch: "#f2ebda" },
  { id: "black-cat", name: "Black cat", kind: "cat", coat: "#454754", patch: "#626778" },
  { id: "calico", name: "Calico cat", kind: "cat", coat: "#eee4d0", patch: "#c07a42" },
  { id: "corgi", name: "Corgi", kind: "dog", coat: "#c7803d", patch: "#eeb76f" },
  { id: "golden", name: "Golden retriever", kind: "dog", coat: "#c68c40", patch: "#efc36c" },
  { id: "dachshund", name: "Dachshund", kind: "dog", coat: "#654331", patch: "#a66738" },
  { id: "rabbit", name: "Rabbit", kind: "rabbit", coat: "#d8cbbf", patch: "#b4a59c" },
  { id: "hamster", name: "Hamster", kind: "hamster", coat: "#dba75a", patch: "#bd8443" },
  { id: "parrot", name: "Parrot", kind: "parrot", coat: "#70ae7c", patch: "#48839f" },
] as const;

const DEFAULT_PET = { pet_id: "tabby", visible: true, animated: true };
type PetState = "idle" | "working" | "listening" | "completed" | "attention";

export function PetSprite({ petId, size = 40, state = "idle", animated = false }: {
  petId: string; size?: number; state?: PetState; animated?: boolean;
}) {
  const pet = PETS.find((p) => p.id === petId) ?? PETS[0];
  const pixels: string[] = [...silhouettes[pet.kind]];
  if (pet.id === "corgi") {
    pixels[1] = "...oo......oo...";
    pixels[2] = "...oao....oao...";
    pixels[3] = "...oaaooooaao...";
  }
  if (pet.id === "dachshund") {
    pixels[9] = "....obbbbbbbooo.";
    pixels[10] = "....obbbbbbbbbo.";
    pixels[11] = "...obbbbbbbbbbo.";
    pixels[12] = "...obbbbbbbbbo..";
    pixels[13] = "...oboooooobbo..";
    pixels[14] = "....oo.....oo...";
  }
  const colors: Record<string, string> = { o: "#332f37", a: pet.coat, b: pet.patch, m: pet.id === "black-cat" ? "#626778" : "#f5e9cf", e: "#24232c", n: "#bb7681", p: "#dfa4a0" };
  return (
    <span aria-hidden="true" data-pet={pet.id} data-state={state} className="pet-sprite inline-flex shrink-0" data-animated={animated} style={{ width: size, height: size }}>
      <svg className="size-full" width={size} height={size} viewBox="0 0 16 16" shapeRendering="crispEdges" focusable="false">
        {pixels.flatMap((row, y) => [...row].map((pixel, x) => {
          if (pixel === ".") return null;
          // Coat markings distinguish the cats without recoloring their eyes.
          const marking = pet.id === "tabby" && pixel === "a" && y < 6 && x % 3 === 0;
          const calico = pet.id === "calico" && pixel === "a" && ((x < 7 && y < 7) || (x > 9 && y > 10));
          return <rect key={`${x}-${y}`} x={x} y={y} width="1" height="1" fill={marking || calico ? pet.patch : colors[pixel]} />;
        }))}
      </svg>
      <style jsx>{`
        .pet-sprite[data-animated="true"][data-state="working"] { animation: pet-bob 1s steps(2) infinite; }
        .pet-sprite[data-animated="true"][data-state="listening"] { animation: pet-listen 1.2s steps(2) infinite; }
        .pet-sprite[data-animated="true"][data-state="completed"] { animation: pet-hop .6s steps(3) 1; }
        .pet-sprite[data-state="attention"] { transform: rotate(-8deg); }
        @keyframes pet-bob { 50% { transform: translateY(-2px); } }
        @keyframes pet-listen { 50% { transform: rotate(8deg); } }
        @keyframes pet-hop { 50% { transform: translateY(-5px); } }
        @media (prefers-reduced-motion: reduce) { .pet-sprite { animation: none !important; transform: none !important; } }
      `}</style>
    </span>
  );
}

export default function PetCompanion({ size = 32, state = "idle", fallback = null, onOpen }: {
  size?: number; state?: PetState; fallback?: React.ReactNode; onOpen?: () => void;
}) {
  const { user } = useUser();
  const preference = user?.pet_preference ?? DEFAULT_PET;
  if (!user || !preference.visible) return <>{fallback}</>;
  return <PetSettingsButton onOpen={onOpen}><PetSprite petId={preference.pet_id} size={size} state={state} animated={preference.animated} /></PetSettingsButton>;
}

const PetSettingsContext = createContext({ open: false, openSettings: () => {} });
export const usePetSettings = () => useContext(PetSettingsContext).openSettings;
export const usePetSettingsOpen = () => useContext(PetSettingsContext).open;

function PetSettingsButton({ children, onOpen }: { children: React.ReactNode; onOpen?: () => void }) {
  const openSettings = usePetSettings();
  return (
    <button type="button" aria-label="Pet settings" title="Pet settings" aria-haspopup="dialog"
      className="inline-flex shrink-0 cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-ring"
      onClick={(event) => { event.stopPropagation(); openSettings(); onOpen?.(); }}>
      {children}
    </button>
  );
}

/** A separate lane above the composer; hover pauses motion to make the pet easy to catch. */
export function PetRunway({ running }: { running: boolean }) {
  const { user } = useUser();
  const preference = user?.pet_preference ?? DEFAULT_PET;
  if (!user || !preference.visible) return null;
  return (
    <div className="pet-runway" data-running={running && preference.animated}>
      <div className="pet-track">
        <div className="pet-runner">
          <div className="pet-facing">
            <PetCompanion size={32} state={running ? "working" : "attention"} />
          </div>
        </div>
      </div>
      <style jsx>{`
        .pet-runway { height: 38px; overflow: hidden; user-select: none; }
        .pet-track { width: calc(100% - 32px); padding-top: 4px; }
        .pet-runner { width: 100%; }
        .pet-facing { width: 32px; height: 32px; pointer-events: auto; }
        .pet-runway[data-running="true"] .pet-runner { animation: pet-run 8s linear infinite; }
        .pet-runway[data-running="true"] .pet-facing { animation: pet-turn 8s steps(1) infinite; }
        .pet-runway:hover .pet-runner, .pet-runway:hover .pet-facing,
        .pet-runway:focus-within .pet-runner, .pet-runway:focus-within .pet-facing { animation-play-state: paused; }
        @keyframes pet-run { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(100%); } }
        @keyframes pet-turn { 0%, 100% { transform: scaleX(1); } 50% { transform: scaleX(-1); } }
        @media (prefers-reduced-motion: reduce) {
          .pet-runner, .pet-facing { animation: none !important; transform: none !important; }
        }
      `}</style>
    </div>
  );
}

export function PetSettingsProvider({ children }: { children: React.ReactNode }) {
  const { user, refresh } = useUser();
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  return (
    <PetSettingsContext.Provider value={{ open, openSettings: () => {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      if (opener.current?.getAttribute("role") === "menuitem") {
        opener.current = document.querySelector<HTMLElement>('[data-slot="dropdown-menu-trigger"][data-state="open"]');
      }
      setOpen(true);
    } }}>
      {children}
      <Dialog open={open && !!user} onOpenChange={setOpen}>
        <DialogContent data-pet-settings="true" className="z-[60] max-h-[85dvh] overflow-y-auto sm:max-w-lg"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (opener.current?.isConnected) opener.current.focus();
          }}>
          <DialogTitle>Your pet companion</DialogTitle>
          <DialogDescription>One little friend, everywhere in Loma. Saved just for you.</DialogDescription>
          {user && <PetPicker key={user.email} initial={user.pet_preference ?? DEFAULT_PET} onSaved={() => { refresh(); setOpen(false); }} />}
        </DialogContent>
      </Dialog>
    </PetSettingsContext.Provider>
  );
}

function PetPicker({ initial, onSaved }: { initial: typeof DEFAULT_PET; onSaved: () => void }) {
  const [draft, setDraft] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Ignore a response if the dialog closed or the authenticated account changed.
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function save() {
    setSaving(true);
    setError("");
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ""}/api/governance/me/pet`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft),
      });
      if (!res.ok) throw new Error("Could not save your pet. Please try again.");
      if (mounted.current) onSaved();
    } catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : "Could not save pet."); }
    finally { if (mounted.current) setSaving(false); }
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2" role="group" aria-label="Choose your pet">
        {PETS.map((pet) => (
          <button type="button" key={pet.id} disabled={saving} aria-pressed={draft.pet_id === pet.id}
            onClick={() => setDraft({ ...draft, pet_id: pet.id })}
            className="flex flex-col items-center gap-1 rounded-lg border border-border p-2 text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring aria-pressed:border-primary aria-pressed:bg-accent disabled:opacity-50">
            <PetSprite petId={pet.id} size={48} />{pet.name}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3 rounded-lg bg-muted p-3">
        <PetSprite petId={draft.pet_id} size={48} state="working" animated={draft.animated} />
        <div className="text-sm"><p className="font-medium">Your new sidekick</p><p className="text-xs text-muted-foreground">A quiet companion while you work.</p></div>
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.visible} disabled={saving} onChange={(e) => setDraft({ ...draft, visible: e.target.checked })} />Show my pet throughout Loma</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.animated} disabled={saving} onChange={(e) => setDraft({ ...draft, animated: e.target.checked })} />Animate reactions</label>
      <p className="text-xs text-muted-foreground">Your device’s reduced-motion setting is always respected.</p>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save pet"}</Button>
    </div>
  );
}
