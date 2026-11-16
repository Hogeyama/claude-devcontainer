// hostexec: run commands on the host for a container, one approval per run.
//
//   hostexec init [--force]
//   hostexec serve [--listen ADDR] [--port N] [--ui-port N] [--map CONTAINER=HOST]...
//   hostexec review [--ui-port N]
//   hostexec ui-url [--ui-port N]
//   hostexec snippet [--port N] [DIR | --global]

import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { apiHandler } from "./api.ts";
import { Approvals } from "./approvals.ts";
import { type PathMap, parseMapping } from "./request.ts";
import { uiHandler } from "./ui.ts";
// @ts-expect-error: plain JavaScript shared with the browser.
import { commandLine, record, shellWord, visible } from "../web/app.js";
import { INHERITED_ENV } from "./run.ts";

const ROOT = join(import.meta.dir, "..");
export const HOST_NAME = "host.docker.internal";
const DEFAULT_PORT = 7878;
const DEFAULT_UI_PORT = 7879;
const DEFAULT_LISTEN = "172.17.0.1";

function configDir(): string {
  const base = process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
  return join(base, "hostexec");
}

const files = (dir = configDir()) => ({
  dir,
  apiToken: join(dir, "api-token"),
  uiToken: join(dir, "ui-token"),
  ca: join(dir, "ca.pem"),
  cert: join(dir, "server.pem"),
  key: join(dir, "server-key.pem"),
});

function die(message: string): never {
  console.error(`hostexec: ${message}`);
  process.exit(2);
}

function options(args: string[], known: Record<string, "value" | "flag" | "list">) {
  const out: Record<string, string | boolean | string[]> = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i] as string;
    const name = a.replace(/^--/, "");
    const kind = a.startsWith("--") ? known[name] : undefined;
    if (!kind) die(`unknown argument ${a}`);
    if (kind === "flag") {
      out[name] = true;
      continue;
    }
    const value = args[++i];
    if (value === undefined) die(`${a} needs a value`);
    if (kind === "list") out[name] = [...((out[name] as string[] | undefined) ?? []), value];
    else out[name] = value;
  }
  return out;
}

function port(value: unknown, fallback: number): number {
  if (value === undefined) return fallback;
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1 || n > 65535) die(`not a port: ${String(value)}`);
  return n;
}

function writeSecret(path: string): void {
  writeFileSync(path, `${randomBytes(32).toString("hex")}\n`, { mode: 0o600 });
  chmodSync(path, 0o600);
}

const readSecret = (path: string): string => {
  try {
    return readFileSync(path, "utf8").trim();
  } catch {
    die(`cannot read ${path}; run \`hostexec init\` first`);
  }
};

function openssl(args: string[], cwd: string): void {
  const r = spawnSync("openssl", args, { cwd, stdio: ["ignore", "ignore", "pipe"] });
  if (r.error || r.status !== 0) {
    die(`openssl ${args[0]} failed: ${r.error?.message ?? r.stderr.toString().trim()}`);
  }
}

function init(args: string[]): void {
  const opts = options(args, { force: "flag" });
  const f = files();
  if (existsSync(f.apiToken) && !opts.force) {
    die(`${f.dir} is already set up; --force makes new keys and tokens`);
  }
  mkdirSync(f.dir, { recursive: true, mode: 0o700 });
  chmodSync(f.dir, 0o700);
  writeSecret(f.apiToken);
  writeSecret(f.uiToken);
  // A CA that can sign for host.docker.internal alone, whose key is thrown
  // away once it has signed the server's certificate: trusting it in the
  // proxy trusts nothing else.
  const caKey = join(f.dir, "ca-key.pem.tmp");
  const csr = join(f.dir, "server.csr.tmp");
  const ext = join(f.dir, "server.ext.tmp");
  try {
    openssl(
      [
        "req", "-x509", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
        "-nodes", "-keyout", caKey, "-out", f.ca, "-days", "3650",
        "-subj", "/CN=hostexec local CA",
        "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", `nameConstraints=critical,permitted;DNS:${HOST_NAME}`,
      ],
      f.dir,
    );
    openssl(
      [
        "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
        "-nodes", "-keyout", f.key, "-out", csr, "-subj", `/CN=${HOST_NAME}`,
      ],
      f.dir,
    );
    writeFileSync(
      ext,
      [
        "basicConstraints=critical,CA:FALSE",
        "keyUsage=critical,digitalSignature",
        "extendedKeyUsage=serverAuth",
        `subjectAltName=DNS:${HOST_NAME}`,
        "",
      ].join("\n"),
    );
    openssl(
      [
        "x509", "-req", "-in", csr, "-CA", f.ca, "-CAkey", caKey,
        "-set_serial", `0x${randomBytes(16).toString("hex")}`,
        "-days", "825", "-sha256", "-extfile", ext, "-out", f.cert,
      ],
      f.dir,
    );
    chmodSync(f.key, 0o600);
  } finally {
    for (const tmp of [caKey, csr, ext]) rmSync(tmp, { force: true });
  }
  console.log(`hostexec: set up in ${f.dir}

Add to ~/.config/claude-devcontainer/credentials.toml:

  [secrets]
  HOSTEXEC_TOKEN = { command = "cat ${f.apiToken}" }

and, for each workspace, what \`hostexec snippet DIR\` prints to
DIR/.devcontainer/claude.local.toml. Then restart the environment, run
\`hostexec serve\` on the host, and in the container:

  curl -fsS https://${HOST_NAME}:${DEFAULT_PORT}/client -o ~/.local/bin/hostexec
  chmod +x ~/.local/bin/hostexec
  hostexec -- uname -a`);
}

function readAssets() {
  const read = (name: string) => readFileSync(join(ROOT, "web", name), "utf8");
  return { html: read("index.html"), js: read("app.js"), css: read("style.css") };
}

function notify(): void {
  // A fixed text: what the container sent never reaches the notification
  // daemon, some of which read markup.
  try {
    const child = spawn("notify-send", ["hostexec", "A command is waiting for approval"], {
      stdio: "ignore",
    });
    child.on("error", () => {});
    child.unref();
  } catch {}
}

function serve(args: string[]): void {
  const opts = options(args, {
    listen: "value",
    port: "value",
    "ui-port": "value",
    map: "list",
    "no-notify": "flag",
  });
  const f = files();
  const apiToken = readSecret(f.apiToken);
  const uiToken = readSecret(f.uiToken);
  const map: PathMap = ((opts.map as string[] | undefined) ?? []).map((spec) => {
    const m = parseMapping(spec);
    return typeof m === "string" ? die(`--map ${m}`) : m;
  });
  const approvals = new Approvals();
  const listen = (opts.listen as string | undefined) ?? DEFAULT_LISTEN;
  const apiPort = port(opts.port, DEFAULT_PORT);
  const uiPort = port(opts["ui-port"], DEFAULT_UI_PORT);

  let api: ReturnType<typeof Bun.serve>;
  try {
    api = Bun.serve({
      hostname: listen,
      port: apiPort,
      // Keepalives keep a held request busy; this only ends dead connections.
      idleTimeout: 60,
      tls: { cert: readFileSync(f.cert, "utf8"), key: readFileSync(f.key, "utf8") },
      fetch: apiHandler({
        token: apiToken,
        approvals,
        map,
        secrets: [apiToken, uiToken],
        client: readFileSync(join(ROOT, "client", "hostexec"), "utf8"),
      }),
    });
  } catch (e) {
    die(`cannot listen on ${listen}:${apiPort}: ${(e as Error).message}`);
  }
  const authority = `127.0.0.1:${uiPort}`;
  try {
    Bun.serve({
      hostname: "127.0.0.1",
      port: uiPort,
      fetch: uiHandler({ authority: () => authority, token: uiToken, approvals, assets: readAssets() }),
    });
  } catch (e) {
    die(`cannot listen on ${authority}: ${(e as Error).message}`);
  }
  approvals.onHold((held) => {
    const cmd = commandLine(held.exec.argv);
    console.error(
      `hostexec: waiting for approval: ${cmd.length > 200 ? `${cmd.slice(0, 200)}…` : cmd}`,
    );
    if (!opts["no-notify"]) notify();
  });
  const stop = () => {
    approvals.denyAll();
    api.stop(true);
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  console.error(`hostexec: taking requests on https://${listen}:${apiPort} (as https://${HOST_NAME}:${apiPort})`);
  for (const [from, to] of map) console.error(`hostexec: ${from} in the container is ${to} here`);
  console.error(`hostexec: approve in a browser: http://${authority}/#${uiToken}`);
  console.error("hostexec: or run `hostexec review` in another terminal");
}

async function review(args: string[]): Promise<void> {
  const opts = options(args, { "ui-port": "value" });
  const token = readSecret(files().uiToken);
  const origin = `http://127.0.0.1:${port(opts["ui-port"], DEFAULT_UI_PORT)}`;
  const api = async (path: string, body: object) => {
    const res = await fetch(origin + path, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: res.ok, data: (await res.json().catch(() => ({}))) as Record<string, unknown> };
  };
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  const asked = new Set<string>();
  console.error("hostexec review: waiting for requests; Ctrl-C to stop");
  for (;;) {
    let pending: unknown[] = [];
    try {
      const { ok, data } = await api("/api/pending", {});
      if (!ok) die(`the server refused: ${String(data.error)}`);
      pending = Array.isArray(data.pending) ? data.pending : [];
    } catch {
      die(`no server at ${origin}; is \`hostexec serve\` running?`);
    }
    const next = pending.map(record).find((r) => r && !asked.has(r.id));
    if (!next) {
      await Bun.sleep(1000);
      continue;
    }
    asked.add(next.id);
    const env = Object.entries(next.env as Record<string, string>);
    console.error(
      [
        "",
        `command:   ${commandLine(next.argv)}`,
        `directory: ${visible(next.cwd)}${next.cwd === next.requestedCwd ? "" : ` (from ${visible(next.requestedCwd)} in the container)`}`,
        `env:       ${INHERITED_ENV.join(", ")} from the host${env.length ? ", plus:" : ", nothing else"}`,
        ...env.map(([k, v]) => `           ${visible(k)}=${shellWord(v)}`),
      ].join("\n"),
    );
    const answer = (await rl.question("Run it on the host? [y/N] ")).trim().toLowerCase();
    const { ok, data } = await api("/api/decision", { id: next.id, approve: answer === "y" || answer === "yes" });
    console.error(ok ? (answer.startsWith("y") ? "approved" : "denied") : `not decided: ${String(data.error)}`);
  }
}

function uiUrl(args: string[]): void {
  const opts = options(args, { "ui-port": "value" });
  console.log(`http://127.0.0.1:${port(opts["ui-port"], DEFAULT_UI_PORT)}/#${readSecret(files().uiToken)}`);
}

/**
 * The proxy settings: for one workspace's claude.local.toml with its path, or
 * (`dir` null) for claude-devcontainer's host mode, whose one claude.toml
 * serves every workspace, with the path from the HOSTEXEC_WORKSPACE secret.
 */
export function snippet(dir: string | null, caPath: string, apiPort: number): string {
  if (dir !== null && (/["\\\p{Cc}]/u.test(dir) || /[^\x20-\x7e]/.test(dir))) {
    die(`${dir}: the path must be printable ASCII without " or \\, to go in a header`);
  }
  return `# hostexec: commands on the host, one approval per run.
[proxy]
ca = "${caPath}"

[proxy.hosts."${HOST_NAME}"]
routes = ["POST /run", "GET /client"]
https = ${apiPort}

[proxy.hosts."${HOST_NAME}".inject]
Authorization = "Bearer \${HOSTEXEC_TOKEN}"
X-Hostexec-Map = "/workspace=${dir ?? "${HOSTEXEC_WORKSPACE}"}"

# The client, read-only and on PATH.
[agent.mounts]
"/usr/bin/hostexec" = "${join(ROOT, "client", "hostexec")}"
`;
}

/** The secrets, for ~/.config/claude-devcontainer/credentials.toml. */
export function secretsSnippet(apiToken: string, global: boolean): string {
  return `[secrets]
HOSTEXEC_TOKEN = { command = "cat ${apiToken}" }
${
  global
    ? `# claude-devcontainer's host command names the workspace in this variable.
HOSTEXEC_WORKSPACE = { command = 'printf %s "$CLAUDE_DEVCONTAINER_WORKSPACE"' }
`
    : ""
}`;
}

function printSnippet(args: string[]): void {
  const dirs = args.filter((a, i) => !a.startsWith("--") && args[i - 1] !== "--port");
  const opts = options(args.filter((a) => !dirs.includes(a)), { port: "value", global: "flag" });
  if (dirs.length > 1 || (opts.global && dirs.length > 0)) die("one directory, or --global");
  const f = files();
  const apiPort = port(opts.port, DEFAULT_PORT);
  if (opts.global) {
    process.stdout.write(`# For ~/.config/claude-devcontainer/claude.toml (claude-devcontainer up):

${snippet(null, f.ca, apiPort)}
# For ~/.config/claude-devcontainer/credentials.toml:

${secretsSnippet(f.apiToken, true)}`);
  } else {
    process.stdout.write(snippet(resolve(dirs[0] ?? "."), f.ca, apiPort));
  }
}

const [command, ...rest] = process.argv.slice(2);
switch (command) {
  case "init":
    init(rest);
    break;
  case "serve":
    serve(rest);
    break;
  case "review":
    await review(rest);
    break;
  case "ui-url":
    uiUrl(rest);
    break;
  case "snippet":
    printSnippet(rest);
    break;
  default:
    console.error(
      "usage: hostexec init [--force]\n" +
        "       hostexec serve [--listen ADDR] [--port N] [--ui-port N] [--map CONTAINER=HOST]... [--no-notify]\n" +
        "       hostexec review [--ui-port N]\n" +
        "       hostexec ui-url [--ui-port N]\n" +
        "       hostexec snippet [--port N] [DIR | --global]",
    );
    process.exit(command === undefined || command === "--help" ? 0 : 2);
}
