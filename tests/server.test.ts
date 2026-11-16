import { afterAll, describe, expect, test } from "bun:test";
import { apiHandler, type Event } from "../src/api.ts";
import { Approvals } from "../src/approvals.ts";
import { uiHandler } from "../src/ui.ts";

const TOKEN = "a".repeat(64);
const UI_TOKEN = "b".repeat(64);
const approvals = new Approvals(2_000);
const api = Bun.serve({
  port: 0,
  hostname: "127.0.0.1",
  fetch: apiHandler({
    token: TOKEN,
    approvals,
    map: [["/workspace", "/"]],
    secrets: [TOKEN],
    client: "#!client",
    keepaliveMs: 50,
  }),
});
let authority = "";
const ui = Bun.serve({
  port: 0,
  hostname: "127.0.0.1",
  fetch: uiHandler({
    authority: () => authority,
    token: UI_TOKEN,
    approvals,
    assets: { html: "<html>", js: "js", css: "css" },
  }),
});
authority = `127.0.0.1:${ui.port}`;
afterAll(() => {
  api.stop(true);
  ui.stop(true);
});

const run = (body: unknown, token = TOKEN) =>
  fetch(`http://127.0.0.1:${api.port}/run`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

const uiCall = (path: string, body: unknown, headers: Record<string, string> = {}) =>
  fetch(`http://${authority}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${UI_TOKEN}`, Origin: `http://${authority}`, ...headers },
    body: JSON.stringify(body),
  });

async function events(res: Response): Promise<Event[]> {
  return (await res.text())
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l));
}

async function waitHeld(): Promise<string> {
  for (let i = 0; i < 100; i++) {
    const { pending } = (await (await uiCall("/api/pending", {})).json()) as { pending: { id: string }[] };
    if (pending[0]) return pending[0].id;
    await Bun.sleep(20);
  }
  throw new Error("nothing held");
}

describe("api", () => {
  test("needs the token", async () => {
    expect((await run({ argv: ["true"], cwd: "/" }, "wrong")).status).toBe(401);
    const res = await fetch(`http://127.0.0.1:${api.port}/client`);
    expect(res.status).toBe(401);
  });

  test("serves the client", async () => {
    const res = await fetch(`http://127.0.0.1:${api.port}/client`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    expect(await res.text()).toBe("#!client");
  });

  test("refuses a bad request before holding it", async () => {
    const res = await run({ argv: ["true"], cwd: "/", extra: 1 });
    expect(res.status).toBe(400);
    expect(approvals.list()).toEqual([]);
  });

  test("approved: runs in the mapped directory and streams the output", async () => {
    const pending = run({ argv: ["sh", "-c", `pwd; echo ${TOKEN}; exit 4`], cwd: "/workspace/tmp" });
    const id = await waitHeld();
    expect((await uiCall("/api/decision", { id, approve: true })).status).toBe(200);
    const ev = await events(await pending);
    expect(ev[0]).toMatchObject({ event: "held", id });
    expect(ev).toContainEqual({ event: "started", cwd: "/tmp" });
    const out = ev
      .filter((e) => e.event === "stdout")
      .map((e) => Buffer.from((e as { data: string }).data, "base64").toString())
      .join("");
    expect(out).toBe("/tmp\n[masked by hostexec]\n");
    expect(ev.at(-1)).toEqual({ event: "exit", exitCode: 4, signal: null });
  });

  test("denied", async () => {
    const pending = run({ argv: ["touch", "/tmp/should-not-exist"], cwd: "/" });
    const id = await waitHeld();
    await uiCall("/api/decision", { id, approve: false });
    const ev = await events(await pending);
    expect(ev.at(-1)).toEqual({ event: "refused", reason: "denied" });
    expect(ev.some((e) => e.event === "started")).toBe(false);
  });

  test("expired, with keepalives while it waits", async () => {
    const ev = await events(await run({ argv: ["true"], cwd: "/" }));
    expect(ev.some((e) => e.event === "keepalive")).toBe(true);
    expect(ev.at(-1)).toEqual({ event: "refused", reason: "expired" });
  });

  test("a client that goes away withdraws its request", async () => {
    const ac = new AbortController();
    const res = await fetch(`http://127.0.0.1:${api.port}/run`, {
      method: "POST",
      headers: { Authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ argv: ["true"], cwd: "/" }),
      signal: ac.signal,
    });
    await waitHeld();
    ac.abort();
    await res.text().catch(() => {});
    for (let i = 0; i < 100 && approvals.list().length; i++) await Bun.sleep(20);
    expect(approvals.list()).toEqual([]);
  });
});

describe("ui", () => {
  test("serves the page to anyone, the API only with token, Origin and Host", async () => {
    expect((await fetch(`http://${authority}/`)).status).toBe(200);
    expect((await uiCall("/api/pending", {})).status).toBe(200);
    expect((await uiCall("/api/pending", {}, { Authorization: "Bearer x" })).status).toBe(403);
    expect((await uiCall("/api/pending", {}, { Origin: "http://evil.example" })).status).toBe(403);
    expect((await uiCall("/api/pending", {}, { "Sec-Fetch-Site": "cross-site" })).status).toBe(403);
    const rebound = await fetch(`http://localhost:${ui.port}/api/pending`, {
      method: "POST",
      headers: { Authorization: `Bearer ${UI_TOKEN}`, Origin: `http://localhost:${ui.port}` },
      body: "{}",
    });
    expect(rebound.status).toBe(403);
  });

  test("a decision for an unknown id is 404", async () => {
    expect((await uiCall("/api/decision", { id: "nope", approve: true })).status).toBe(404);
  });
});
