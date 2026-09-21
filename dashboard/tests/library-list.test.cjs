const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

function load(rel, extra = {}) {
  const module = { exports: {} };
  const context = vm.createContext({
    exports: module.exports,
    module,
    require: (id) => {
      if (id === "react") return React;
      if (id === "react/jsx-runtime") return require("react/jsx-runtime");
      let nested = null;
      if (id.startsWith("@/")) nested = path.join("../src", id.slice(2));
      else if (id.startsWith("./") || id.startsWith("../")) {
        nested = path.join(path.dirname(rel), id);
      }
      if (nested) {
        const candidates = ["", ".ts", ".tsx"].map((ext) => nested + ext);
        const hit = candidates.find((p) => fs.existsSync(path.join(__dirname, p)));
        if (hit) return load(hit, extra);
      }
      return require(id);
    },
    process: { env: {} },
    fetch: extra.fetch,
    URLSearchParams,
  });
  const src = fs.readFileSync(path.join(__dirname, rel), "utf8");
  const { outputText } = ts.transpileModule(src, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
    },
    fileName: rel,
  });
  vm.runInContext(outputText, context);
  return module.exports;
}

const sample = [
  {
    file_id: "new",
    name: "chart.png",
    mime_type: "image/png",
    size_bytes: 2048,
    created_at: "2026-09-20T11:00:00Z",
    conversation_id: "conv-1",
    source: "opencode",
  },
  {
    file_id: "old",
    name: "report.pdf",
    mime_type: "application/pdf",
    size_bytes: 16,
    created_at: "2026-09-20T10:00:00Z",
    conversation_id: "conv-1",
    source: "opencode",
  },
];

test("fetchAssets sends no query by default and accepts a query object", async () => {
  const urls = [];
  const api = load("../src/lib/assets-api.ts", {
    fetch: async (url) => {
      urls.push(url);
      return {
        ok: true,
        json: async () => ({ assets: sample, query: {} }),
      };
    },
  });
  const none = await api.fetchAssets();
  assert.equal(urls[0], "/api/assets");
  assert.deepEqual(none.assets.map((a) => a.name), ["chart.png", "report.pdf"]);
  assert.deepEqual(none.query, {});

  await api.fetchAssets({ q: "chart" });
  assert.equal(urls[1], "/api/assets?q=chart");
});

test("library rows render from a mocked fetch", async () => {
  const api = load("../src/lib/assets-api.ts", {
    fetch: async () => ({
      ok: true,
      json: async () => ({ assets: sample, query: {} }),
    }),
  });
  const data = await api.fetchAssets();
  const { LibrarySurface } = load("../src/app/library/LibrarySurface.tsx");
  const html = renderToStaticMarkup(React.createElement(LibrarySurface, { assets: data.assets }));
  assert.match(html, /chart\.png/);
  assert.match(html, /aria-label="PNG"/);
  assert.match(html, /2\.0 KB/);
  assert.match(html, /2026-09-20T11:00:00Z/);
  assert.match(html, /report\.pdf/);
  assert.match(html, /aria-label="PDF"/);
  assert.doesNotMatch(html, /No files yet/);
});

test("library list scroller can shrink inside the pane", () => {
  const src = fs.readFileSync(path.join(__dirname, "../src/app/library/page.tsx"), "utf8");
  assert.match(src, /flex h-full min-h-0 overflow-hidden/);
  assert.match(src, /flex flex-col min-h-0 overflow-y-auto/);
});

test("library empty state renders when there are no assets", () => {
  const { LibrarySurface } = load("../src/app/library/LibrarySurface.tsx");
  const html = renderToStaticMarkup(React.createElement(LibrarySurface, { assets: [] }));
  assert.match(html, /No files yet/);
  assert.doesNotMatch(html, /chart\.png/);
});

test("unavailable row renders its reason; available row does not", () => {
  const { LibrarySurface } = load("../src/app/library/LibrarySurface.tsx");
  const html = renderToStaticMarkup(React.createElement(LibrarySurface, {
    assets: [
      { ...sample[0], status: "available", reason: null },
      {
        ...sample[1],
        status: "unavailable",
        reason: "bytes missing at /tmp/loma-served-files/old.pdf",
      },
    ],
  }));
  assert.match(html, /bytes missing at \/tmp\/loma-served-files\/old\.pdf/);
  assert.match(html, /chart\.png/);
  assert.doesNotMatch(html, /bytes missing at \/tmp\/loma-served-files\/new/);
});
