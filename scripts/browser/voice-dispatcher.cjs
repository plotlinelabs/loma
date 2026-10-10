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
  // Record what crosses the real voice data channel, so the live run can show
  // which events were sent and what the model said back.
  await page.addInitScript(() => {
    window.liveSent = [];
    window.liveEvents = [];
    const create = RTCPeerConnection.prototype.createDataChannel;
    RTCPeerConnection.prototype.createDataChannel = function (...args) {
      const channel = create.apply(this, args);
      const send = channel.send.bind(channel);
      channel.send = (data) => {
        try {
          const e = JSON.parse(data);
          window.liveSent.push(e);
          if (e.type === "session.commentary.append") window.liveAnnouncedAt = window.liveEvents.length;
        } catch {}
        return send(data);
      };
      channel.addEventListener("message", ({ data }) => {
        try {
          const e = JSON.parse(data);
          if (e.type !== "session.usage.updated") window.liveEvents.push(e);
        } catch {}
      });
      return channel;
    };
  });
  const live = {};
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
        "Save a draft task with the exact instructions: Reply with only the word OK and do nothing else. Do not start it.",
      );
    await page.getByPlaceholder("Type to Loma...").press("Enter");
    await page
      .getByText("Saved draft:", { exact: false })
      .waitFor({ timeout: 60000 });
    await page.screenshot({ path: out + "/live-draft.png" });
    // Real run, real voice model: start a task, hear about it when it stops,
    // open it, then mark it done. Each step is recorded rather than asserted,
    // since it depends on the provider and a live agent run.
    const say = async (text) => {
      await page.getByText("Listening", { exact: true }).waitFor({ timeout: 120000 });
      await page.getByPlaceholder("Type to Loma...").fill(text);
      await page.getByPlaceholder("Type to Loma...").press("Enter");
    };
    const step = async (name, fn) => {
      try {
        live[name] = (await fn()) ?? true;
      } catch (e) {
        live[name] = false;
        live[name + "_error"] = String(e.message || e).slice(0, 300);
      }
    };
    let liveDraftId;
    await step("started", async () => {
      const draftId = await page.evaluate(() => {
        const outputs = liveSent.filter(e => e.item?.type === "function_call_output").map(e => JSON.parse(e.item.output));
        return outputs.find(o => o.state === "draft")?.id;
      });
      assert(draftId, "saved draft id returned");
      liveDraftId = draftId;
      await say(`Start the existing draft with id ${draftId}. Do not create another task.`);
      await page.getByText("Started:", { exact: false }).waitFor({ timeout: 90000 });
      assert(await page.evaluate(() => liveEvents.some(e => e.event?.item?.name === "start_task")), "real model calls start_task");
    });
    assert(live.started, live.started_error);
    await step("announcedByApp", async () => {
      await page.waitForFunction(
        () => window.liveSent.some((e) => e.type === "session.commentary.append"),
        null, { timeout: 420000 });
      return page.evaluate(() => window.liveSent.find((e) => e.type === "session.commentary.append"));
    });
    await step("spokenByModel", async () => {
      // Captions that arrive after the announcement was sent, with no user turn in between.
      const from = await page.evaluate(() => window.liveAnnouncedAt);
      await page.waitForFunction(
        (n) => window.liveEvents.slice(n).some((e) => e.type === "session.output_transcript.delta"),
        from, { timeout: 60000 });
      await page.waitForTimeout(8000);
      return page.evaluate((n) => window.liveEvents.slice(n)
        .filter((e) => e.type === "session.output_transcript.delta").map((e) => e.delta).join(""), from);
    });
    await page.screenshot({ path: out + "/live-announcement.png" });
    await step("opened", async () => {
      await say(`Open the task with id ${liveDraftId} on screen.`);
      await page.getByText("Opened:", { exact: false }).waitFor({ timeout: 90000 });
      await page.getByRole("dialog").waitFor({ timeout: 15000 });
      await page.waitForTimeout(2500);
      await page.screenshot({ path: out + "/live-open-task.png" });
    });
    await step("closed", async () => {
      // The drawer is modal and keeps keyboard focus, so real typing would not
      // reach the voice composer behind it. Set the text and Enter on it
      // directly; a real user would simply say this.
      const box = page.getByPlaceholder("Type to Loma...");
      await page.getByText("Listening", { exact: true }).waitFor({ timeout: 120000 });
      const sentBefore = await page.evaluate(() => liveSent.length);
      await box.evaluate((el, text) => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(el, text);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }, "Close the task that is open on my screen.");
      await page.waitForTimeout(300);
      await box.dispatchEvent("keydown", { key: "Enter", bubbles: true });
      await page.waitForFunction((n) => liveSent.length > n, sentBefore, { timeout: 15000 });
      await page.getByText("Closed:", { exact: false }).waitFor({ timeout: 90000 });
      await page.getByRole("dialog").waitFor({ state: "hidden", timeout: 15000 });
      assert(await page.evaluate(() => liveEvents.some(e => e.event?.item?.name === "close_task")), "real model calls close_task");
      await page.waitForTimeout(1500);
      await page.screenshot({ path: out + "/live-close-task.png" });
    });
    if (await page.getByRole("dialog").count()) {
      await page.keyboard.press("Escape");
      await page.getByRole("dialog").waitFor({ state: "hidden", timeout: 15000 });
    }
    await step("markedDone", async () => {
      await say(`Mark the task with id ${liveDraftId} done.`);
      await page.getByText("Moved to Done:", { exact: false }).waitFor({ timeout: 90000 });
      await page.waitForTimeout(1500);
      await page.screenshot({ path: out + "/live-moved-done.png" });
      return page.evaluate(async () => {
        const board = await (await fetch("/api/tasks")).json();
        return board.tasks.filter((t) => t.column === "done").map((t) => t.title || t.prompt);
      });
    });
    live.errors = await page.evaluate(() => window.liveEvents.filter((e) => e.type === "error"));
    fs.writeFileSync(out + "/live-flow.json", JSON.stringify(live, null, 2));
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
  // The typed turn ends, then the delegated backend calls the newer tools.
  const completed = () =>
    page.evaluate(() => voiceEmit({ type: "response.event", event: { type: "response.completed" } }));
  await completed();
  const call = async (callId, name, args) => {
    await page.evaluate(([callId, name, args]) => {
      voiceEmit({ type: "session.delegation.created" });
      voiceEmit({
        type: "response.event",
        event: {
          type: "response.output_item.done",
          item: { type: "function_call", call_id: callId, name, arguments: JSON.stringify(args) },
        },
      });
      voiceEmit({ type: "response.event", event: { type: "response.completed" } });
    }, [callId, name, args]);
    await page.waitForFunction(
      (id) => voiceSent.some((e) => e.item?.call_id === id), callId, { timeout: 30000 });
    const output = await page.evaluate(
      (id) => voiceSent.find((e) => e.item?.call_id === id).item.output, callId);
    await completed();
    return JSON.parse(output);
  };
  // By id: reruns against the same database leave older drafts with the same words.
  const draft = task.id;
  const columnOf = () => page.evaluate(async (id) => {
    const board = await (await fetch("/api/tasks")).json();
    const found = board.tasks.find((t) => t.conversation_id === id);
    return { column: found.column, id, lanes: board.lanes };
  }, draft);

  const listed = await call("list1", "list_tasks", { column: "all" });
  assert(listed.lanes.length > 0, "list_tasks names the board's lanes");
  assert(listed.tasks.some((t) => t.lane), "staged tasks say which lane they are in");
  const before = await columnOf();
  const target = before.lanes.find((l) => l.id !== before.column);
  if (target) {
    const moved = await call("move1", "move_task", { task: draft, to: target.name });
    assert.equal(moved.moved_to, target.name);
    assert.equal((await columnOf()).column, target.id, "the task really moved lanes");
    await page.getByText(`Moved to ${target.name}:`, { exact: false }).waitFor();
  }
  // The board's rules come back as a spoken reason, not a silent failure.
  const refused = await call("move2", "move_task", { task: draft, to: "done" });
  assert.match(refused.error, /draft/);
  assert.match((await call("move3", "move_task", { task: draft, to: "nowhere" })).error, /Done/);
  assert.notEqual((await columnOf()).column, "done");
  await page.waitForTimeout(600);
  await page.screenshot({ path: out + "/desktop-move.png" });

  // A real shared-board card starred onto the personal board. Voice must patch
  // only the bookmark, even while the source task remains an unstarted draft.
  const shared = await page.evaluate(async () => {
    const request = async (url, method, body) => {
      const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(JSON.stringify(data));
      return data;
    };
    const { board } = await request("/api/tasks/boards", "POST", { name: "Voice bookmark QA" });
    const { task } = await request("/api/tasks", "POST", { board: board.id, prompt: "Equipment inventory", start: false });
    await request(`/api/tasks/${task.conversation_id}/star`, "PUT", {});
    return { id: task.conversation_id, board: board.id };
  });
  const starredDone = await call("starDone", "move_task", { task: shared.id, to: "done" });
  assert.equal(starredDone.scope, "your bookmark only");
  assert.equal(starredDone.moved_to, "Done");
  const source = await page.evaluate(async ({ id, board }) => {
    const data = await (await fetch(`/api/tasks?board=${board}`)).json();
    return data.tasks.find(t => t.conversation_id === id);
  }, shared);
  assert.equal(source.task_status, "todo", "bookmark completion never completes source");
  assert.equal((await call("starReopen", "move_task", { task: shared.id, to: "reopen" })).ok, true);
  const starLane = await call("starLane", "move_task", { task: shared.id, to: before.lanes[0].name });
  assert.equal(starLane.ok, true);
  await page.screenshot({ path: out + "/desktop-starred-move.png" });

  // Starting reuses the saved draft's data. Mock only the expensive chat run;
  // draft reads and attachment storage still use the authenticated backend.
  const configured = await page.evaluate(async () => {
    const res = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: "Inspect equipment list", start: false, model: "openai/gpt-6-luna",
        tool_config: { enabled_tools: [], enabled_skills: [] },
        files: [{ name: "equipment.txt", type: "text", mimetype: "text/plain", data: btoa("laptop") }] }) });
    const data = await res.json();
    if (!res.ok) throw new Error(JSON.stringify(data));
    return data.task.conversation_id;
  });
  let startedBody;
  await page.route("**/api/chat", route => {
    startedBody = route.request().postDataJSON();
    return route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
  });
  assert.equal((await call("startSaved", "start_task", { task: configured })).state, "queued");
  assert.equal(startedBody.conversation_id, configured);
  assert.equal(startedBody.message, "Inspect equipment list");
  assert.equal(startedBody.model, "openai/gpt-6-luna");
  assert.equal(startedBody.files[0].data, Buffer.from("laptop").toString("base64"));
  assert.deepEqual(startedBody.tool_config, { enabled_tools: [], enabled_skills: [] });
  assert.match((await call("startSavedAgain", "start_task", { task: configured })).error, /already been sent/);
  await page.unroute("**/api/chat");
  await page.screenshot({ path: out + "/desktop-start-saved.png" });

  const opened = await call("open1", "open_task", { task: draft });
  assert.equal(opened.shown, "screen");
  await page.getByRole("dialog").waitFor({ timeout: 15000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: out + "/desktop-open-task.png" });
  // Voice is still on behind the drawer.
  assert(await page.getByTestId("voice-panel").count());
  // close_task shuts the drawer; the task and the voice session are untouched.
  const closed = await call("close1", "close_task", {});
  assert.equal(closed.closed, true);
  assert.equal(closed.title, opened.title);
  await page.getByRole("dialog").waitFor({ state: "hidden", timeout: 15000 });
  assert(await page.getByTestId("voice-panel").count());
  await page.getByText("Closed:", { exact: false }).first().waitFor();
  await page.waitForTimeout(800);
  await page.screenshot({ path: out + "/desktop-close-task.png" });
  const closedAgain = await call("close2", "close_task", {});
  assert.equal(closedAgain.closed, false);
  assert.match(closedAgain.reason, /No task is open/);
  // Opening and closing again works.
  await call("open1b", "open_task", { task: draft });
  await page.getByRole("dialog").waitFor({ timeout: 15000 });
  assert.equal((await call("close3", "close_task", {})).closed, true);
  await page.getByRole("dialog").waitFor({ state: "hidden", timeout: 15000 });

  // A watched task stops running: the board says so on its own. The run is
  // faked at the HTTP layer so the timing is deterministic.
  const { id: draftId } = await columnOf();
  let phase = "running";
  const isBoard = (u) => u.pathname === "/api/tasks";
  await page.route(isBoard, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch();
    const body = await res.json();
    for (const t of body.tasks) {
      if (t.conversation_id !== draftId) continue;
      t.task_status = "active";
      t.column = phase === "running" ? "working" : "needs_input";
      t.status = phase === "running" ? "running" : "completed";
    }
    await route.fulfill({ response: res, json: body });
  });
  const isDraft = (u) => u.pathname === `/api/conversations/${draftId}`;
  await page.route(isDraft, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.conversation.final_response =
      "**2 issues** found in `voice.ts`. Details in [the PR](https://example.com/pr/1).";
    await route.fulfill({ response: res, json: body });
  });
  await page.waitForTimeout(9000); // at least one board check sees it running
  assert.equal(
    await page.evaluate(() => voiceSent.filter((e) => e.type === "session.commentary.append").length), 0,
    "nothing is announced while the task still runs");
  phase = "stopped";
  await page.waitForFunction(
    () => voiceSent.some((e) => e.type === "session.commentary.append"), null, { timeout: 20000 });
  const spoken = await page.evaluate(
    () => voiceSent.filter((e) => e.type === "session.commentary.append"));
  assert.equal(spoken.length, 1);
  assert.equal(spoken[0].delegation_id, null);
  // The title can be the async-generated one by now, so match around it.
  assert.match(spoken[0].content, /^Update from the board: the task ".+" just finished\./);
  assert.match(spoken[0].content, /It says: 2 issues found in voice\.ts\. Details in the PR\. Want to see it\?$/);
  assert.doesNotMatch(spoken[0].content, /https?:|\*|`/);
  await page.getByText("Finished:", { exact: false }).waitFor();
  await page.evaluate(() => voiceEmit({
    type: "session.output_transcript.delta",
    delta: "Review voice dispatcher just finished: two issues found. Want to see it?",
    start_ms: 60000,
    end_ms: 63000,
  }));
  await page.waitForTimeout(700);
  await page.screenshot({ path: out + "/desktop-announcement.png" });
  // Told once: later checks of the same board stay quiet.
  await page.waitForTimeout(9000);
  assert.equal(
    await page.evaluate(() => voiceSent.filter((e) => e.type === "session.commentary.append").length), 1);
  await page.unroute(isBoard);
  await page.unroute(isDraft);

  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Start voice mode", exact: true })
    .click();
  await page.getByText("Listening", { exact: true }).waitFor();
  // Phones have no drawer: open_task leaves a link instead of navigating away.
  const linked = await call("open2", "open_task", { task: draft });
  assert.equal(linked.shown, "link");
  await page.getByText("Tap to open:", { exact: false }).waitFor();
  // Nothing is open on a phone board, so there is nothing to close.
  assert.equal((await call("close4", "close_task", {})).closed, false);
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
        liveFlow: live,
        checks: [
          "desktop idle",
          "draft creation",
          "duplicate event suppressed",
          "mute/unmute",
          "typed dispatcher input",
          "list names lanes",
          "move between lanes",
          "blocked moves give a reason",
          "open task in drawer",
          "close task drawer, and nothing-open reason",
          "finish announced once",
          "mobile open leaves a link",
          "mobile layout",
          "end restores composer",
          "provider failure restores typing",
          "starred card done/reopen/lane moves leave source unchanged",
          "start saved draft preserves instructions, model, tools and attachments",
        ],
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS: 17 browser checks. Provider signaling status:",
    signal.status(),
  );
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
