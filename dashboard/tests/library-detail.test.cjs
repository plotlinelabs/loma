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
      if (id.endsWith("/lib/api") || id.endsWith("/lib/api.ts") || id === "../../../lib/api") {
        return {
          getFileUrl: (fileId) => `/api/files/${fileId}`,
          basePath: "",
        };
      }
      if (id.includes("ArtifactViewer")) {
        return {
          default: ({ artifact }) =>
            React.createElement("div", {
              "data-preview-language": artifact.language,
              "data-file-url": artifact.file_url,
            }, artifact.title),
        };
      }
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

const pdf = {
  file_id: "old",
  name: "report.pdf",
  mime_type: "application/pdf",
  size_bytes: 16,
  created_at: "2026-09-20T10:00:00Z",
  conversation_id: "conv-1",
  source: "opencode",
};

const png = {
  file_id: "new",
  name: "chart.png",
  mime_type: "image/png",
  size_bytes: 2048,
  created_at: "2026-09-20T11:00:00Z",
  conversation_id: "conv-1",
  source: "opencode",
};

test("selected asset resume and view links carry the source conversation", () => {
  const { LibraryDetail } = load("../src/app/library/LibraryDetail.tsx");
  const html = renderToStaticMarkup(
    React.createElement(LibraryDetail, { asset: pdf, onClose() {} }),
  );
  assert.match(html, /href="\/chat\?continue=conv-1"/);
  assert.match(html, /href="\/conversations\/conv-1"/);
});

test("download control targets the existing serve URL", () => {
  const { LibraryDetail } = load("../src/app/library/LibraryDetail.tsx");
  const html = renderToStaticMarkup(
    React.createElement(LibraryDetail, { asset: pdf, onClose() {} }),
  );
  assert.match(html, /data-file-url="\/api\/files\/old"/);
});

test("preview routing chooses the viewer by mime type", () => {
  const { LibraryDetail } = load("../src/app/library/LibraryDetail.tsx");
  const documentHtml = renderToStaticMarkup(
    React.createElement(LibraryDetail, { asset: pdf, onClose() {} }),
  );
  assert.match(documentHtml, /data-preview-language="pdf"/);
  assert.match(documentHtml, /data-file-url="\/api\/files\/old"/);

  const imageHtml = renderToStaticMarkup(
    React.createElement(LibraryDetail, { asset: png, onClose() {} }),
  );
  assert.match(imageHtml, /data-preview-language="png"/);
  assert.doesNotMatch(imageHtml, /data-preview-language="(pdf|docx|pptx)"/);
  assert.match(imageHtml, /data-file-url="\/api\/files\/new"/);
});

test("unavailable asset disables open and download and shows the reason", () => {
  const { LibraryDetail } = load("../src/app/library/LibraryDetail.tsx");
  const html = renderToStaticMarkup(React.createElement(LibraryDetail, {
    asset: {
      ...pdf,
      status: "unavailable",
      reason: "bytes missing at /tmp/loma-served-files/old.pdf",
    },
    onClose() {},
  }));
  assert.match(html, /bytes missing at \/tmp\/loma-served-files\/old\.pdf/);
  assert.doesNotMatch(html, /data-file-url=/);
  assert.doesNotMatch(html, /data-preview-language=/);
});

test("available asset enables open and download", () => {
  const { LibraryDetail } = load("../src/app/library/LibraryDetail.tsx");
  const html = renderToStaticMarkup(React.createElement(LibraryDetail, {
    asset: { ...pdf, status: "available", reason: null },
    onClose() {},
  }));
  assert.match(html, /data-file-url="\/api\/files\/old"/);
  assert.doesNotMatch(html, /bytes missing/);
});
