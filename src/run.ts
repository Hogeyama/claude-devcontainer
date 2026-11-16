// Running an approved command on the host, streaming its output.
//
// Process-group handling adapted from strait (nix-agent-sandbox/contrib/strait, MIT).

import { spawn } from "node:child_process";
import type { ExecRequest } from "./request.ts";

/**
 * Taken from the server's own environment, so that `nix` or `docker` resolve
 * as in the host shell. Nothing else is inherited.
 */
export const INHERITED_ENV = ["PATH", "HOME"] as const;

/** How long a stopped command's processes get after SIGTERM before SIGKILL. */
export const KILL_GRACE_MS = 5_000;

export function execEnv(
  req: ExecRequest,
  host: NodeJS.ProcessEnv,
): Record<string, string> {
  const env: Record<string, string> = {};
  for (const name of INHERITED_ENV) {
    const v = host[name];
    if (v !== undefined) env[name] = v;
  }
  return { ...env, ...req.env };
}

export type Stream = "stdout" | "stderr";

export interface Exit {
  exitCode: number | null;
  signal: string | null;
}

/**
 * Run the command with no stdin. Output goes to `onOutput` as it comes, with
 * `secrets` masked. The command, and the process group it runs in, is stopped
 * when `signal` aborts.
 */
export function runOnHost(
  req: ExecRequest,
  secrets: readonly string[],
  onOutput: (stream: Stream, data: Buffer) => void,
  signal?: AbortSignal,
  graceMs = KILL_GRACE_MS,
): Promise<Exit> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve({ exitCode: null, signal: null });
    const maskers = {
      stdout: new Masker(secrets),
      stderr: new Masker(secrets),
    };
    const emit = (stream: Stream, data: Buffer) => {
      if (data.length > 0) onOutput(stream, data);
    };
    // In a process group of its own, so that stopping it reaches what it
    // started too. A child that makes its own group (setsid) still escapes.
    const child = spawn(req.argv[0] as string, req.argv.slice(1), {
      cwd: req.cwd,
      env: execEnv(req, process.env),
      stdio: ["ignore", "pipe", "pipe"],
      detached: true,
    });
    own(child.pid);
    let stopped: Promise<void> | undefined;
    const stop = () => {
      stopped = stopGroup(child.pid, graceMs);
    };
    signal?.addEventListener("abort", stop, { once: true });
    child.stdout.on("data", (c: Buffer) => emit("stdout", maskers.stdout.push(c)));
    child.stderr.on("data", (c: Buffer) => emit("stderr", maskers.stderr.push(c)));
    let done = false;
    const finish = (r: Exit, extra = "") => {
      // A spawn failure can emit both "error" and "close".
      if (done) return;
      done = true;
      signal?.removeEventListener("abort", stop);
      void (stopped ?? Promise.resolve()).then(() => {
        if (child.pid !== undefined) owned.delete(child.pid);
        emit("stdout", maskers.stdout.end());
        emit("stderr", maskers.stderr.end());
        if (extra) emit("stderr", Buffer.from(extra));
        resolve(r);
      });
    };
    child.on("error", (e) =>
      finish({ exitCode: 127, signal: null }, `hostexec: ${e.message}\n`),
    );
    child.on("close", (code, sig) => finish({ exitCode: code, signal: sig }));
  });
}

const MASK = Buffer.from("[masked by hostexec]");

/**
 * Replaces secrets in a byte stream that arrives in chunks: it holds back
 * enough of each chunk's tail that a secret split across chunks is still
 * found.
 */
export class Masker {
  private readonly secrets: Buffer[];
  private readonly keep: number;
  private pending = Buffer.alloc(0);

  constructor(secrets: readonly string[]) {
    this.secrets = secrets
      .filter((s) => s.length >= 8)
      .map((s) => Buffer.from(s));
    this.keep = Math.max(0, ...this.secrets.map((s) => s.length - 1));
  }

  push(chunk: Buffer): Buffer {
    if (this.secrets.length === 0) return chunk;
    const data = this.replace(Buffer.concat([this.pending, chunk]));
    // Only a tail that could be the start of a secret is held back, so
    // output that ends a line or a word passes at once.
    const cut = data.length - this.partial(data);
    this.pending = data.subarray(cut);
    return data.subarray(0, cut);
  }

  /** The length of the longest tail of `data` that starts some secret. */
  private partial(data: Buffer): number {
    for (let k = Math.min(this.keep, data.length); k > 0; k--) {
      const tail = data.subarray(data.length - k);
      if (this.secrets.some((s) => s.subarray(0, k).equals(tail))) return k;
    }
    return 0;
  }

  end(): Buffer {
    const rest = this.pending;
    this.pending = Buffer.alloc(0);
    return rest;
  }

  private replace(data: Buffer): Buffer {
    let out = data;
    for (const secret of this.secrets) {
      let at = out.indexOf(secret);
      if (at === -1) continue;
      const parts: Buffer[] = [];
      let from = 0;
      while (at !== -1) {
        parts.push(out.subarray(from, at), MASK);
        from = at + secret.length;
        at = out.indexOf(secret, from);
      }
      parts.push(out.subarray(from));
      out = Buffer.concat(parts);
    }
    return out;
  }
}

/** Groups of runs not yet over, by process group ID. */
const owned = new Set<number>();
let exitHook = false;

/** Have the server's own exit SIGKILL every group still running. */
function own(pgid: number | undefined): void {
  if (pgid === undefined) return;
  owned.add(pgid);
  if (exitHook) return;
  exitHook = true;
  process.on("exit", () => {
    for (const g of owned) {
      try {
        process.kill(-g, "SIGKILL");
      } catch {}
    }
  });
}

/** SIGTERM the group, then SIGKILL it if still there after `graceMs`. */
function stopGroup(pgid: number | undefined, graceMs: number): Promise<void> {
  if (pgid === undefined) return Promise.resolve();
  const send = (sig: NodeJS.Signals | 0) => {
    try {
      process.kill(-pgid, sig);
      return true;
    } catch {
      return false;
    }
  };
  if (!send("SIGTERM")) return Promise.resolve();
  const deadline = Date.now() + graceMs;
  return new Promise((done) => {
    const poll = setInterval(() => {
      const there = send(0);
      if (there && Date.now() < deadline) return;
      // A group seen gone is not signalled again: its ID may be reused.
      if (there) send("SIGKILL");
      clearInterval(poll);
      done();
    }, 50);
  });
}
