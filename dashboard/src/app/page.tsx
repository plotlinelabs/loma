"use client";

import ChatWithArtifacts from "../components/ChatWithArtifacts";

export default function Home() {
  return (
    <div className="h-[calc(100vh-3rem)] flex flex-col -mx-6 lg:-mx-8 -my-6">
      <ChatWithArtifacts />
    </div>
  );
}
