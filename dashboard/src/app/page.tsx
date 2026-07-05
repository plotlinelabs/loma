"use client";

import ChatWithArtifacts from "../components/ChatWithArtifacts";

export default function Home() {
  return (
    // Phones: fill the layout's flex slot — the bottom nav takes real height,
    // so a 100dvh-based height overflows. Desktop keeps the original sizing.
    <div className="h-[calc(100dvh-3rem)] max-md:h-auto max-md:flex-1 max-md:min-h-0 flex flex-col -mx-6 lg:-mx-8 -my-6 max-md:-my-3">
      <ChatWithArtifacts />
    </div>
  );
}
