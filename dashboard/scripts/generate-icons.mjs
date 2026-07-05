// Generate PWA icons from the square favicon source.
//
//   mkdir -p public/icons && node scripts/generate-icons.mjs
//
// Requires sharp (devDependency). iOS paints transparency black on
// apple-touch-icons, so those (and maskable icons) are flattened onto the
// brand background; standard icons keep their alpha.
import sharp from "sharp";

// favicon.svg is the canonical Loma mark (favicon.png is stale). Rasterize
// at high density so resizes stay crisp.
const SRC = "public/favicon.svg";
const BG = "#1F1D1A"; // warm black brand background
const src = (size) => sharp(SRC, { density: (72 * size) / 160 });

// Standard icons — transparency is fine on Android/desktop.
await src(192).resize(192, 192).png().toFile("public/icons/icon-192.png");
await src(512).resize(512, 512).png().toFile("public/icons/icon-512.png");

// Maskable — keep the mark inside the ~80% safe zone, opaque background.
await src(410)
  .resize(410, 410)
  .extend({ top: 51, bottom: 51, left: 51, right: 51, background: BG })
  .flatten({ background: BG })
  .png()
  .toFile("public/icons/maskable-512.png");

// Apple touch icon — 180x180, MUST be opaque.
await src(150)
  .resize(150, 150)
  .extend({ top: 15, bottom: 15, left: 15, right: 15, background: BG })
  .flatten({ background: BG })
  .png()
  .toFile("public/icons/apple-touch-icon.png");

console.log("icons written to public/icons/");
