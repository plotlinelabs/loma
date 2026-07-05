import type { MetadataRoute } from "next";

// basePath supports /pr/N preview deployments; empty in production.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Loma",
    short_name: "Loma",
    description: "Self-hosted AI agent factory for company teams",
    // The tasks board is the notification-driven surface; push clicks deep-link
    // into /chat regardless of start_url.
    start_url: `${basePath}/tasks`,
    scope: `${basePath}/`,
    display: "standalone",
    background_color: "#FFFFFF",
    theme_color: "#F5F5F4",
    icons: [
      { src: `${basePath}/icons/icon-192.png`, sizes: "192x192", type: "image/png" },
      { src: `${basePath}/icons/icon-512.png`, sizes: "512x512", type: "image/png" },
      {
        src: `${basePath}/icons/maskable-512.png`,
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
