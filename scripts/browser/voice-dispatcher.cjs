const { chromium } = require("playwright");
const fs = require("fs");
const assert = require("node:assert/strict");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const env = Object.fromEntries(
  fs
    .readFileSync(root + "/dashboard/.env", "utf8")
    .split("\n")
    .filter((l) => /^[A-Z_]+=/.test(l))
    .map((l) => {
      let i = l.indexOf("=");
      return [l.slice(0, i), l.slice(i + 1).replace(/^'|'$/g, "")];
    }),
);
const backend = fs.readFileSync(root + "/.env", "utf8");
assert(
  /OBSERVABILITY_DB_NAME=['"]?loma_local_/.test(backend),
  "Isolated database required",
);
assert(/LOMA_ENABLE_SLACK=['"]?false/.test(backend));
assert(/LOMA_ENABLE_SCHEDULER=['"]?false/.test(backend));
assert.equal(
  process.env.LOMA_VOICE_E2E,
  "1",
  "Explicit live-provider opt-in required",
);
const out = process.env.LOMA_VOICE_EVIDENCE;
assert(out, "Evidence directory required");
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({
    ...(process.env.LOMA_CHROMIUM_PATH
      ? { executablePath: process.env.LOMA_CHROMIUM_PATH }
      : {}),
    args: [
      "--no-sandbox",
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
    ],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await page.goto("http://localhost:13001/login");
  await page.waitForLoadState("networkidle");
  await page.fill("#signin-email", env.USER_NAME);
  await page.fill("#signin-password", env.PASSWORD);
  await page.fill("#setup-token", env.LOMA_SETUP_TOKEN);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.includes("/login"), {
    timeout: 60000,
  });
  await page.goto("http://localhost:13001/tasks");
  await page
    .getByRole("button", { name: "Start voice mode", exact: true })
    .waitFor({ timeout: 60000 });
  await page.screenshot({ path: out + "/desktop-idle.png" });
  // Real provider signaling with a silent synthetic microphone, never a human recording.
  await page
    .getByRole("button", { name: "Start voice mode", exact: true })
    .click();
  const signal = await page.waitForResponse(
    (r) => r.url().includes("/api/voice/session"),
    { timeout: 60000 },
  );
  const result = await signal.json();
  fs.writeFileSync(
    out + "/live-signaling.json",
    JSON.stringify(
      {
        status: signal.status(),
        error: result.error || null,
        hasAnswer: !!result.sdp,
        syntheticMicrophone: true,
      },
      null,
      2,
    ),
  );
  if (signal.status() === 201) {
    await page
      .getByText("Listening", { exact: true })
      .waitFor({ timeout: 30000 });
    await page
      .getByPlaceholder("Type to Loma...")
      .fill(
        "Save a draft task: Check the voice dispatcher UI. Do not start it.",
      );
    await page.getByPlaceholder("Type to Loma...").press("Enter");
    await page
      .getByText("Saved draft:", { exact: false })
      .waitFor({ timeout: 60000 });
    await page.screenshot({ path: out + "/live-draft.png" });
    await page.getByRole("button", { name: "End", exact: true }).click();
    await page.waitForTimeout(6000);
  }
  await page.screenshot({ path: out + "/provider-result.png" });
  // Deterministic WebRTC double. Task CRUD still reaches the isolated backend.
  await page.addInitScript(() => {
    class Channel extends EventTarget {
      readyState = "open";
      send(text) {
        const e = JSON.parse(text);
        window.voiceSent.push(e);
        if (e.type === "session.close")
          setTimeout(() => window.voiceEmit({ type: "session.closed" }), 0);
      }
      close() {
        this.readyState = "closed";
      }
    }
    class Peer extends EventTarget {
      iceGatheringState = "complete";
      connectionState = "connected";
      localDescription = { sdp: "test-offer" };
      addTrack() {}
      createDataChannel() {
        window.voiceChannel = new Channel();
        return window.voiceChannel;
      }
      async createOffer() {
        return { sdp: "test-offer", type: "offer" };
      }
      async setLocalDescription() {}
      async setRemoteDescription() {
        setTimeout(() => window.voiceEmit({ type: "session.started" }), 0);
      }
      close() {}
    }
    window.RTCPeerConnection = Peer;
    window.voiceSent = [];
    window.voiceEmit = (e) =>
      window.voiceChannel.dispatchEvent(
        new MessageEvent("message", { data: JSON.stringify(e) }),
      );
  });
  await page.route("**/api/voice/session", (r) =>
    r.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ sdp: "test-answer" }),
    }),
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Start voice mode", exact: true })
    .click();
  await page.getByText("Listening", { exact: true }).waitFor();
  await page.evaluate(() => {
    voiceEmit({
      type: "session.input_transcript.delta",
      delta: "Save a task to review the voice dispatcher.",
      start_ms: 0,
      end_ms: 500,
    });
    voiceEmit({ type: "session.delegation.created" });
    voiceEmit({
      type: "response.event",
      event: {
        type: "response.output_item.done",
        item: {
          type: "function_call",
          call_id: "draft1",
          name: "create_task",
          arguments: JSON.stringify({
            prompt: "Review voice dispatcher",
            start: false,
          }),
        },
      },
    });
    voiceEmit({
      type: "response.event",
      event: { type: "response.completed" },
    });
  });
  await page
    .getByText("Saved draft: Review voice dispatcher", { exact: true })
    .waitFor({ timeout: 30000 });
  let task = await page.evaluate(() =>
    JSON.parse(
      window.voiceSent.find((e) => e.item?.call_id === "draft1").item.output,
    ),
  );
  assert(task.ok);
  // A duplicated event must not create another task.
  await page.evaluate(() =>
    voiceEmit({
      type: "response.event",
      event: {
        type: "response.output_item.done",
        item: {
          type: "function_call",
          call_id: "draft1",
          name: "create_task",
          arguments: '{"prompt":"duplicate","start":false}',
        },
      },
    }),
  );
  assert.equal(
    await page.getByText("Saved draft: duplicate", { exact: true }).count(),
    0,
  );
  await page.evaluate(() => {
    voiceEmit({
      type: "response.event",
      event: { type: "response.completed" },
    });
    voiceEmit({
      type: "session.output_transcript.delta",
      delta: "Saved a draft in your first lane.",
      start_ms: 500,
      end_ms: 1800,
    });
  });
  await page.getByRole("button", { name: "Mute", exact: true }).click();
  assert.equal(
    await page
      .getByRole("button", { name: "Unmute", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  await page.screenshot({ path: out + "/desktop-active.png" });
  await page.getByRole("button", { name: "Unmute", exact: true }).click();
  await page.getByPlaceholder("Type to Loma...").fill("Which tasks need me?");
  await page.getByPlaceholder("Type to Loma...").press("Enter");
  assert(
    await page.evaluate(() =>
      voiceSent.some(
        (e) =>
          e.item?.role === "user" &&
          e.item.content[0].text === "Which tasks need me?",
      ),
    ),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Start voice mode", exact: true })
    .click();
  await page.getByText("Listening", { exact: true }).waitFor();
  await page.waitForTimeout(800);
  await page.screenshot({ path: out + "/mobile-active.png" });
  assert(await page.getByPlaceholder("Type to Loma...").isVisible());
  await page.getByRole("button", { name: "End", exact: true }).click();
  await page
    .getByRole("button", { name: "Start voice mode", exact: true })
    .waitFor();
  await page.screenshot({ path: out + "/mobile-idle.png" });
  await page.unroute("**/api/voice/session");
  await page.route("**/api/voice/session", (r) =>
    r.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "Voice is unavailable. Try again later." }),
    }),
  );
  await page
    .getByRole("button", { name: "Start voice mode", exact: true })
    .click();
  await page
    .getByText("Voice is unavailable. Try again later.", { exact: true })
    .waitFor();
  assert(await page.getByPlaceholder("What do you need done?").isVisible());

  fs.writeFileSync(
    out + "/browser-result.json",
    JSON.stringify(
      {
        passed: true,
        mockedTransport: true,
        realIsolatedTaskCRUD: true,
        liveProviderDraftCreated: signal.status() === 201,
        checks: [
          "desktop idle",
          "draft creation",
          "duplicate event suppressed",
          "mute/unmute",
          "typed dispatcher input",
          "mobile layout",
          "end restores composer",
          "provider failure restores typing",
        ],
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS: 8 browser checks. Provider signaling status:",
    signal.status(),
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
