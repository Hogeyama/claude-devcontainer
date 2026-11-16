import { describe, expect, test } from "bun:test";
import { Approvals } from "../src/approvals.ts";
import { mapPath, parseExecRequest, parseMapping } from "../src/request.ts";
import { Masker, runOnHost } from "../src/run.ts";
// @ts-expect-error: plain JavaScript shared with the browser.
import { commandLine, record, shellWord, visible } from "../web/app.js";

describe("parseExecRequest", () => {
  const ok = (body: unknown) => {
    const r = parseExecRequest(JSON.stringify(body));
    if (typeof r === "string") throw new Error(r);
    return r;
  };
  const bad = (text: string) => {
    const r = parseExecRequest(text);
    expect(typeof r).toBe("string");
    return r as string;
  };

  test("takes argv, cwd and env", () => {
    expect(ok({ argv: ["ls", "-l"], cwd: "/tmp", env: { A: "1" } })).toEqual({
      argv: ["ls", "-l"],
      cwd: "/tmp",
      requestedCwd: "/tmp",
      env: { A: "1" },
    });
    expect(ok({ argv: ["ls"], cwd: "/" }).env).toEqual({});
  });

  test("refuses anything else", () => {
    bad("not json");
    bad("[]");
    bad('{"argv":[],"cwd":"/"}');
    bad('{"argv":[""],"cwd":"/"}');
    bad('{"argv":["a\\u0000b"],"cwd":"/"}');
    bad('{"argv":["ls"],"cwd":"relative"}');
    bad('{"argv":["ls"],"cwd":"/","shell":true}');
    bad('{"argv":["ls"],"cwd":"/","env":{"A-B":"1"}}');
    bad('{"argv":["ls"],"cwd":"/","env":{"A":1}}');
    expect(bad('{"argv":["ls"],"argv":["rm"],"cwd":"/"}')).toContain("duplicated");
    expect(bad('{"argv":["ls"],"cwd":"/","env":{"A":"1","A":"2"}}')).toContain("duplicated");
  });

  test("translates cwd through the map", () => {
    const r = parseExecRequest('{"argv":["ls"],"cwd":"/workspace/sub"}', [["/workspace", "/home/u/repo"]]);
    expect(r).toMatchObject({ cwd: "/home/u/repo/sub", requestedCwd: "/workspace/sub" });
  });
});

describe("mapPath", () => {
  const map = [
    ["/workspace", "/home/u/repo"],
    ["/workspace/deep", "/srv/deep"],
  ] as const;
  test("longest prefix wins, on whole components", () => {
    expect(mapPath("/workspace", map)).toBe("/home/u/repo");
    expect(mapPath("/workspace/a/b", map)).toBe("/home/u/repo/a/b");
    expect(mapPath("/workspace/deep/x", map)).toBe("/srv/deep/x");
    expect(mapPath("/workspace2", map)).toBe("/workspace2");
    expect(mapPath("/etc", map)).toBe("/etc");
  });
  test("resolves .. before mapping", () => {
    expect(mapPath("/workspace/../etc", map)).toBe("/etc");
    expect(mapPath("/workspace/a/../../workspace/b", map)).toBe("/home/u/repo/b");
  });
  test("parseMapping", () => {
    expect(parseMapping("/workspace/=/home/u/repo/")).toEqual(["/workspace", "/home/u/repo"]);
    expect(typeof parseMapping("/workspace")).toBe("string");
    expect(typeof parseMapping("rel=/x")).toBe("string");
  });
});

describe("Masker", () => {
  const SECRET = "0123456789abcdef";
  const run = (chunks: string[]) => {
    const m = new Masker([SECRET]);
    const out = chunks.map((c) => m.push(Buffer.from(c)).toString());
    return out.join("") + m.end().toString();
  };
  test("masks whole and split secrets", () => {
    expect(run([`a${SECRET}b`])).toBe("a[masked by hostexec]b");
    expect(run(["xx0123", "456789ab", "cdefyy"])).toBe("xx[masked by hostexec]yy");
    expect(run(["0123456789abcde"])).toBe("0123456789abcde");
  });
  test("holds back only what could start a secret", () => {
    const m = new Masker([SECRET]);
    expect(m.push(Buffer.from("tick 1\n")).toString()).toBe("tick 1\n");
    expect(m.push(Buffer.from("x 012")).toString()).toBe("x ");
    expect(m.push(Buffer.from("9")).toString()).toBe("0129");
  });
  test("passes through with no secrets", () => {
    const m = new Masker([]);
    expect(m.push(Buffer.from("abc")).toString()).toBe("abc");
  });
});

describe("runOnHost", () => {
  const req = (argv: string[], env: Record<string, string> = {}) => ({
    argv,
    cwd: "/",
    requestedCwd: "/",
    env,
  });
  const collect = async (r: ReturnType<typeof req>, signal?: AbortSignal) => {
    const out = { stdout: "", stderr: "" };
    const exit = await runOnHost(r, ["SECRETSECRET"], (s, d) => { out[s] += d.toString(); }, signal);
    return { exit, out: `stdout:${out.stdout}stderr:${out.stderr}` };
  };
  test("streams output and exit code, with only PATH, HOME and what was asked", async () => {
    const { exit, out } = await collect(
      req(["sh", "-c", 'echo "$FOO $SECRET_ENV"; echo SECRETSECRET >&2; exit 3'], { FOO: "bar" }),
    );
    expect(exit).toEqual({ exitCode: 3, signal: null });
    expect(out).toContain("stdout:bar \n");
    expect(out).toContain("stderr:[masked by hostexec]");
  });
  test("a missing command is 127", async () => {
    const { exit, out } = await collect(req(["/nonexistent/cmd"]));
    expect(exit.exitCode).toBe(127);
    expect(out).toContain("ENOENT");
  });
  test("abort stops the process group", async () => {
    const ac = new AbortController();
    setTimeout(() => ac.abort(), 200);
    const started = Date.now();
    const { exit } = await collect(req(["sh", "-c", "sleep 30 & sleep 30; wait"]), ac.signal);
    expect(exit.signal).toBe("SIGTERM");
    expect(Date.now() - started).toBeLessThan(5000);
  });
});

describe("Approvals", () => {
  const exec = { argv: ["true"], cwd: "/", requestedCwd: "/", env: {} };
  test("decide, expire, withdraw", async () => {
    const a = new Approvals(100);
    const one = a.hold(exec);
    expect(a.list().map((h) => h.id)).toEqual([one.held.id]);
    expect(a.decide(one.held.id, true)).toBe(true);
    expect(await one.decision).toBe("approved");
    expect(a.decide(one.held.id, false)).toBe(false);

    expect(await a.hold(exec).decision).toBe("expired");

    const ac = new AbortController();
    const three = a.hold(exec, ac.signal);
    ac.abort();
    expect(await three.decision).toBe("gone");
    expect(a.list()).toEqual([]);
  });
});

describe("display", () => {
  test("shell words show where each argument ends", () => {
    expect(commandLine(["ls", "-l", "a b", "it's", "x\ny", "‮"])).toBe(
      "ls -l 'a b' $'it\\'s' $'x\\ny' $'\\u202e'",
    );
    expect(shellWord("a\\b")).toBe("'a\\b'");
    expect(visible("a\u001b[31m\\")).toBe("a\\u{1b}[31m\\\\");
  });
  test("record rejects partial data", () => {
    const good = { id: "x", argv: ["ls"], cwd: "/", requestedCwd: "/", env: {}, since: 1, expiresAt: 2 };
    expect(record(good)).toEqual(good);
    expect(record({ ...good, argv: [] })).toBeNull();
    expect(record({ ...good, env: { A: 1 } })).toBeNull();
  });
});
