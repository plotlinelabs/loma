const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const source = fs.readFileSync(path.join(__dirname, "../src/lib/voice-links.ts"), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const mod = { exports: {} };
new Function("module", "exports", "require", outputText)(mod, mod.exports, require);
const { safeLinkUrl, linkKind, collectLinks, resolveLinkRef, readTaskLinks, openLinkInNewTab } = mod.exports;

const PR = "https://github.com/plotlinelabs/loma/pull/412";
const DOC = "https://docs.google.com/document/d/abc/edit";
const SHEET = "https://docs.google.com/spreadsheets/d/xyz/edit";
const raw = (href, label = href, reply = 0) => ({ href, label, reply });

test("safeLinkUrl only allows absolute http(s) URLs", () => {
  assert.equal(safeLinkUrl(PR), PR);
  assert.equal(safeLinkUrl("  HTTPS://Example.com/a  "), "https://example.com/a");
  assert.equal(safeLinkUrl("http://example.com"), "http://example.com/");
  for (const bad of [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd",
    "ftp://example.com", "mailto:a@b.c", "/chat?continue=1", "//evil.com/x", "example.com", "",
    "https://user:pass@example.com/", "https://", "https://exa mple.com", "https://example.com/\nx",
    "vbscript:msgbox", "blob:https://example.com/uuid",
  ]) assert.equal(safeLinkUrl(bad), null, bad);
});

test("linkKind names the common link types", () => {
  assert.equal(linkKind(PR), "pull request");
  assert.equal(linkKind("https://github.com/o/r/issues/9"), "GitHub issue");
  assert.equal(linkKind(DOC), "Google Doc");
  assert.equal(linkKind(SHEET), "Google Sheet");
  assert.equal(linkKind("https://docs.google.com/presentation/d/1"), "Google Slides");
  assert.equal(linkKind("https://drive.google.com/file/d/1"), "Drive file");
  assert.equal(linkKind("https://linear.app/plotline/issue/PLO-1/x"), "Linear ticket");
  assert.equal(linkKind("https://www.notion.so/page-1"), "Notion page");
  assert.equal(linkKind("https://github.com/o/r"), "link");
});

test("collectLinks drops unsafe links, dedupes and keeps order, labels and recency", () => {
  const links = collectLinks([
    raw(PR, PR, 0),
    raw("javascript:alert(1)", "click me", 0),
    raw("/relative", "relative", 1),
    raw(DOC, "Spec doc", 1),
    raw(PR, "PR #412", 2),
    raw("https://example.com/" + "x".repeat(300), "", 2),
  ]);
  assert.deepEqual(links.map((l) => l.href), [PR, DOC, "https://example.com/" + "x".repeat(300)]);
  assert.equal(links[0].label, "PR #412", "a raw-URL label gives way to a descriptive one");
  assert.equal(links[0].lastSeen, 4, "a repeat makes the link recent");
  assert.equal(links[1].label, "Spec doc");
  assert.ok(links[2].label.startsWith("example.com/x") && links[2].label.length <= 120);
  assert.deepEqual(collectLinks([]), []);
});

test("zero links: nothing to open", () => {
  assert.deepEqual(resolveLinkRef([], "the link"), { none: "This task's replies have no links." });
});

test("one link: a generic reference opens it", () => {
  const links = collectLinks([raw(PR, "PR #412")]);
  for (const ref of ["the link", "open it", "", "the PR", "pull request", "the first link", "that one"]) {
    assert.equal(resolveLinkRef(links, ref).link?.href, PR, ref);
  }
  assert.match(resolveLinkRef(links, "the doc").none, /no Google Doc link/);
  assert.match(resolveLinkRef(links, "the second link").none, /only 1 matching link/);
});

test("multiple links: generic references ask, specific ones resolve", () => {
  const links = collectLinks([raw(PR, "PR #412", 0), raw(DOC, "Spec doc", 1), raw(SHEET, "Pricing sheet", 2)]);
  const asked = resolveLinkRef(links, "open the link");
  assert.deepEqual(asked.ambiguous.map((l) => l.href), [PR, DOC, SHEET]);
  assert.equal(resolveLinkRef(links, "the PR").link.href, PR);
  assert.equal(resolveLinkRef(links, "the google doc").link.href, DOC);
  assert.equal(resolveLinkRef(links, "the spreadsheet").link.href, SHEET);
  assert.equal(resolveLinkRef(links, "the first link").link.href, PR);
  assert.equal(resolveLinkRef(links, "second one").link.href, DOC);
  assert.equal(resolveLinkRef(links, "number 3").link.href, SHEET);
  assert.equal(resolveLinkRef(links, "number three").link.href, SHEET);
  assert.equal(resolveLinkRef(links, "link 2").link.href, DOC);
  assert.equal(resolveLinkRef(links, "2").link.href, DOC);
  assert.equal(resolveLinkRef(links, "the latest link").link.href, SHEET);
  assert.equal(resolveLinkRef(links, "pricing").link.href, SHEET);
  assert.match(resolveLinkRef(links, "the fifth link").none, /only 3/);
  assert.match(resolveLinkRef(links, "the figma file").none, /No link .* matches/);
});

test("several links of the asked kind still ask; ordinals count within the kind", () => {
  const PR2 = "https://github.com/plotlinelabs/loma/pull/413";
  const links = collectLinks([raw(DOC, "Spec"), raw(PR, "PR #412"), raw(PR2, "PR #413")]);
  assert.deepEqual(resolveLinkRef(links, "the PR").ambiguous.map((l) => l.href), [PR, PR2]);
  assert.equal(resolveLinkRef(links, "the second PR").link.href, PR2);
  assert.equal(resolveLinkRef(links, "PR 413").link.href, PR2);
  assert.equal(resolveLinkRef(links, "the latest PR").link.href, PR2);
  assert.equal(resolveLinkRef(links, "the latest").link.href, PR2);
});

test("without a browser there is no open task and no tab", () => {
  assert.equal(readTaskLinks(), null);
  assert.equal(openLinkInNewTab(PR), false);
});

function withWindow(open, fn) {
  const before = global.window;
  global.window = { open };
  try { return fn(); } finally { global.window = before; }
}

test("openLinkInNewTab reports a blocked popup so the caller shows a chip", () => {
  const calls = [];
  const blocked = withWindow((...args) => { calls.push(args); return null; }, () => openLinkInNewTab(PR));
  assert.equal(blocked, false);
  assert.deepEqual(calls, [["about:blank", "_blank"]]);
});

test("openLinkInNewTab detaches the opener and navigates to the validated URL only", () => {
  const appended = [];
  const tab = {
    opener: "parent",
    document: { createElement: () => ({}), head: { appendChild: (el) => appended.push(el) } },
    location: { replace(url) { this.url = url; } },
  };
  let opened = 0;
  assert.equal(withWindow(() => { opened++; return tab; }, () => openLinkInNewTab(PR)), true);
  assert.equal(tab.opener, null);
  assert.equal(tab.location.url, PR);
  assert.deepEqual(appended, [{ name: "referrer", content: "no-referrer" }]);
  assert.equal(withWindow(() => { opened++; return tab; }, () => openLinkInNewTab("javascript:alert(1)")), false);
  assert.equal(opened, 1, "an unsafe URL never opens a window");
});

test("openLinkInNewTab closes the tab and falls back when navigation throws", () => {
  let closed = false;
  const tab = {
    document: { createElement: () => ({}), head: null },
    location: { replace() { throw new Error("blocked"); } },
    close() { closed = true; },
  };
  assert.equal(withWindow(() => tab, () => openLinkInNewTab(PR)), false);
  assert.equal(closed, true);
});
