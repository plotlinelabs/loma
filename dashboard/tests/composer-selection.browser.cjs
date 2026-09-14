// Run with the isolated run-loma-local dashboard env loaded, Playwright on NODE_PATH.
const { chromium } = require("playwright");
const { MongoClient } = require("mongodb");
const assert = require("node:assert/strict");
const fs = require("node:fs");
(async () => {
  const origin = "http://localhost:13001";
  assert.ok(
    (process.env.OBSERVABILITY_DB_NAME || "").startsWith("loma_local_"),
  );
  const client = await new MongoClient(
    process.env.OBSERVABILITY_MONGODB_URI,
  ).connect();
  const db = client.db(process.env.OBSERVABILITY_DB_NAME);
  await db
    .collection("conversations")
    .deleteMany({ title: "Composer browser QA" });
  const fixtures = [
    ["qa-alpha", "Alpha support", "workspace", "Support"],
    ["qa-beta", "Beta support", "workspace", "Support"],
    ["qa-personal", "Personal support", "personal", "Support"],
    ["qa-loose", "Loose helper", "workspace", null],
  ];
  for (let n = 0; n < 35; n++)
    fixtures.push([
      `qa-long-${n}`,
      `Long list skill ${n}`,
      "system",
      "Long list",
    ]);
  for (const [slug, name, scope, folder] of fixtures)
    await db
      .collection("skills")
      .updateOne(
        { slug },
        {
          $set: {
            name,
            scope,
            folder,
            description: "Local QA fixture",
            tags: ["fixture"],
            enabled: true,
            created_by: process.env.USER_NAME,
          },
        },
        { upsert: true },
      );
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    page.setDefaultTimeout(20000);
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`${origin}/login`, { timeout: 90000 });
    await page.waitForLoadState("networkidle");
    await page.fill("#signin-email", process.env.USER_NAME);
    await page.fill("#signin-password", process.env.PASSWORD);
    await page.fill("#setup-token", process.env.LOMA_SETUP_TOKEN);
    await page.click("button[type=submit]");
    await page.waitForURL((u) => !u.pathname.includes("/login"), {
      timeout: 60000,
    });
    await page.goto(`${origin}/chat`, { timeout: 90000 });
    const pick = (title) =>
      page.getByRole("dialog", { name: `${title} selection`, exact: true });
    const open = async (title) => {
      await page
        .getByRole("button", { name: new RegExp(`^${title}:`) })
        .last()
        .click();
      await pick(title)
        .getByRole("checkbox", {
          name: `All available ${title.toLowerCase()}`,
          exact: true,
        })
        .waitFor();
    };
    await open("Skills");
    let panel = pick("Skills");
    await panel
      .getByRole("checkbox", { name: "Select Support", exact: true })
      .uncheck();
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Select Workspace", exact: true })
        .getAttribute("aria-checked"),
      "mixed",
    );
    await panel.getByRole("button", { name: /^Support/ }).click();
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Alpha support", exact: true })
        .isChecked(),
      false,
    );
    await panel
      .getByRole("checkbox", { name: "Alpha support", exact: true })
      .check();
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Select Support", exact: true })
        .getAttribute("aria-checked"),
      "mixed",
    );
    await panel
      .getByRole("textbox", { name: "Search skills" })
      .fill("Personal support");
    assert.equal(
      await panel.getByRole("checkbox", { name: /^Select / }).count(),
      0,
    );
    await panel
      .getByRole("button", { name: "Clear results", exact: true })
      .click();
    await panel.getByRole("textbox", { name: "Search skills" }).fill("support");
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Alpha support", exact: true })
        .isChecked(),
      true,
    );
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Beta support", exact: true })
        .isChecked(),
      false,
    );
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Personal support", exact: true })
        .isChecked(),
      false,
    );
    await panel.getByRole("textbox", { name: "Search skills" }).fill("");
    await page.screenshot({
      path: "/tmp/composer-skills-desktop.png",
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await open("Tools");
    panel = pick("Tools");
    await panel
      .getByRole("checkbox", { name: "All available tools", exact: true })
      .uncheck();
    await panel.getByRole("button", { name: /Built-in/ }).click();
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Bash", exact: true })
        .isDisabled(),
      true,
    );
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "Read", exact: true })
        .isChecked(),
      true,
    );
    await page.screenshot({
      path: "/tmp/composer-tools-desktop.png",
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await open("Skills");
    panel = pick("Skills");
    await panel
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .check();
    await panel
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .uncheck();
    await panel
      .getByRole("textbox", { name: "Search skills" })
      .fill("Alpha support");
    const alpha = panel.getByRole("checkbox", {
      name: "Alpha support",
      exact: true,
    });
    await alpha.focus();
    await page.keyboard.press("Space");
    assert.equal(await alpha.isChecked(), true);
    await page.keyboard.press("Escape");
    // Capture the real submit payload without running an external AI agent.
    let sent;
    await page.route("**/api/chat", async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'data: {"type":"text","text":"Local browser fixture response"}\n\n',
      });
    });
    await page
      .getByPlaceholder("What do you need to get done?", { exact: true })
      .fill("Local composer QA");
    await page
      .getByPlaceholder("What do you need to get done?", { exact: true })
      .press("Enter");
    await page.waitForTimeout(600);
    assert.deepEqual(sent.tool_config, {
      enabled_skills: ["qa-alpha"],
      enabled_tools: ["Bash", "Read"],
    });
    await page
      .getByPlaceholder("Ask the agent something...", { exact: true })
      .waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Skills: 1", exact: true })
        .count(),
      1,
    );
    // Mobile create/edit dialog saves through the real task API, no start requested.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${origin}/tasks`, { timeout: 90000 });
    await page.getByRole("button", { name: /^To.?do/i }).click();
    await page.getByRole("button", { name: "Task", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "New task", exact: true });
    await dialog.locator("#task-title").fill("Composer browser QA");
    await dialog
      .getByRole("button", { name: "Skills: All", exact: true })
      .click();
    panel = pick("Skills");
    await panel
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .uncheck();
    await page.waitForTimeout(400);
    console.log("mobile tree", await panel.getByRole("checkbox").count());
    await page.screenshot({
      path: "/tmp/composer-mobile-task.png",
      fullPage: true,
    });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.keyboard.press("Escape");
    await dialog
      .getByRole("button", { name: /^Add/, exact: false })
      .filter({ hasNotText: "start" })
      .click();
    await dialog.waitFor({ state: "hidden" });
    let task = await db
      .collection("conversations")
      .findOne({
        title: "Composer browser QA",
        user_email: process.env.USER_NAME,
      });
    if (!task)
      task = await db
        .collection("conversations")
        .findOne({ title: "Composer browser QA" });
    assert.deepEqual(task.tool_config.enabled_skills, []);
    await page
      .getByText("Composer browser QA", { exact: true })
      .first()
      .click();
    const edit = page.getByRole("dialog", {
      name: "Task details",
      exact: true,
    });
    await edit.waitFor();
    await edit.getByRole("button", { name: /^Skills:/ }).click();
    panel = pick("Skills");
    await panel
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .check();
    await page.keyboard.press("Escape");
    await edit.getByRole("button", { name: "Cancel", exact: true }).click();
    await page
      .getByText("Composer browser QA", { exact: true })
      .first()
      .click();
    await edit.getByRole("button", { name: /^Skills:/ }).click();
    panel = pick("Skills");
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "All available skills", exact: true })
        .isChecked(),
      false,
    );
    await panel
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .check();
    await page.keyboard.press("Escape");
    await edit
      .getByRole("button", { name: /^Save/ })
      .filter({ hasNotText: "start" })
      .click();
    await edit.waitFor({ state: "hidden" });
    task = await db
      .collection("conversations")
      .findOne({ conversation_id: task.conversation_id });
    assert.equal(task.tool_config.enabled_skills, null);
    // Existing conversation and task drawer restore real saved configuration.
    await db
      .collection("conversations")
      .updateOne(
        { conversation_id: task.conversation_id },
        {
          $set: {
            tool_config: {
              enabled_skills: [],
              enabled_tools: ["Bash", "Read"],
            },
          },
        },
      );
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${origin}/chat?continue=${task.conversation_id}`, {
      timeout: 90000,
    });
    await open("Skills");
    assert.equal(
      await pick("Skills")
        .getByRole("checkbox", { name: "All available skills", exact: true })
        .isChecked(),
      false,
    );
    await page.keyboard.press("Escape");
    await page.goto(`${origin}/tasks`, { timeout: 90000 });
    await page
      .getByText("Composer browser QA", { exact: true })
      .first()
      .click();
    await page
      .getByRole("button", { name: /^Skills:/ })
      .last()
      .click();
    await pick("Skills")
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .waitFor();
    assert.equal(
      await pick("Skills")
        .getByRole("checkbox", { name: "All available skills", exact: true })
        .isChecked(),
      false,
    );
    await page.screenshot({
      path: "/tmp/composer-task-drawer.png",
      fullPage: true,
    });
    await page.goto(`${origin}/tasks`, { timeout: 90000 });
    // Quick-add sends both domains. Suppress execution only, preserving the real API save.
    let quickPayload;
    await page.route("**/api/tasks", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      quickPayload = route.request().postDataJSON();
      await route.continue({
        postData: JSON.stringify({
          ...quickPayload,
          start: false,
          title: "Composer quick QA",
        }),
      });
    });
    await open("Skills");
    await pick("Skills")
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .uncheck();
    await page.keyboard.press("Escape");
    const quick = page.locator("textarea").last();
    await quick.fill("Quick capture QA");
    await quick.press("Enter");
    await page.waitForTimeout(1000);
    assert.deepEqual(quickPayload.tool_config.enabled_skills, []);
    assert.equal(quickPayload.start, true);
    assert.ok(
      await db
        .collection("conversations")
        .findOne({
          title: "Composer quick QA",
          "tool_config.enabled_skills": [],
        }),
    );
    // Failed catalogs retain the saved configuration and can be retried.
    let attempts = 0;
    await page.route("**/api/available-tools", async (route) => {
      attempts++;
      if (attempts === 1)
        return route.fulfill({ status: 503, body: "unavailable" });
      return route.continue();
    });
    await page.goto(`${origin}/chat?continue=${task.conversation_id}`, {
      timeout: 90000,
    });
    await page.getByRole("button", { name: /^Skills:/ }).click();
    await pick("Skills")
      .getByRole("button", { name: "Retry", exact: true })
      .click();
    panel = pick("Skills");
    await panel
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .waitFor();
    assert.equal(
      await panel
        .getByRole("checkbox", { name: "All available skills", exact: true })
        .isChecked(),
      false,
    );
    await panel
      .getByRole("checkbox", { name: "All available skills", exact: true })
      .check();
    await panel
      .getByRole("textbox", { name: "Search skills" })
      .fill("Long list");
    const list = panel.locator(".overflow-y-auto");
    assert.ok(await list.evaluate((el) => el.scrollHeight > el.clientHeight));
    await list.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await panel
      .getByRole("textbox", { name: "Search skills" })
      .fill("no-such-result");
    await panel.getByText("No matches.", { exact: true }).waitFor();
    await panel
      .getByRole("textbox", { name: "Search skills" })
      .fill("Alpha support");
    await panel
      .getByRole("button", { name: "Clear results", exact: true })
      .click();
    await panel
      .getByRole("checkbox", { name: "Selected only", exact: true })
      .check();
    await panel.getByText("No matches.", { exact: true }).waitFor();
    await page.keyboard.press("Escape");
    assert.deepEqual(errors, []);
    console.log(
      "PASS: hierarchy, tri-state, required tools, filtered bulk actions, keyboard, new/reply chat payload, mobile create/edit/cancel/reset, existing chat, task drawer, quick-add payload/save, catalog retry, long-list scrolling, selected-only, no page errors.",
    );
  } finally {
    await browser.close();
    await client.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
