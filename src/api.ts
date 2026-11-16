// The server the container reaches, through its proxy, as
// https://host.docker.internal:PORT. The proxy adds the API token, which the
// container never sees, so a request without it came some other way (another
// container on the same bridge, say) and is refused.
//
//   POST /run     {argv, cwd, env}  → NDJSON events, see `Event`
//                 X-Hostexec-Map: CONTAINER=HOST, which the proxy adds as well,
//                 says where this workspace's container paths are on the host
//   GET  /client                    → the Python client, to install in the container

import { createHash, timingSafeEqual } from "node:crypto";
import type { Approvals } from "./approvals.ts";
import { MAX_BODY, type PathMap, parseExecRequest, parseMapping } from "./request.ts";

export const MAP_HEADER = "x-hostexec-map";
import { runOnHost } from "./run.ts";

export const KEEPALIVE_MS = 10_000;

export type Event =
  | { event: "held"; id: string }
  | { event: "keepalive" }
  | { event: "refused"; reason: "denied" | "expired" }
  | { event: "started"; cwd: string }
  | { event: "stdout" | "stderr"; data: string }
  | { event: "exit"; exitCode: number | null; signal: string | null };

export interface ApiOptions {
  token: string;
  approvals: Approvals;
  map: PathMap;
  /** Masked in command output. */
  secrets: readonly string[];
  client: string;
  keepaliveMs?: number;
}

export function sameSecret(given: string | null, want: string): boolean {
  if (given === null) return false;
  const h = (s: string) => createHash("sha256").update(s).digest();
  return timingSafeEqual(h(given), h(want));
}

const text = (body: string, status: number) =>
  new Response(`${body}\n`, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });

export function apiHandler(options: ApiOptions): (req: Request) => Promise<Response> {
  const keepaliveMs = options.keepaliveMs ?? KEEPALIVE_MS;
  return async (req) => {
    const url = new URL(req.url);
    if (!sameSecret(req.headers.get("authorization"), `Bearer ${options.token}`)) {
      return text("hostexec: no or wrong token (is the request going through the proxy?)", 401);
    }
    if (url.search !== "") return text("hostexec: no query is taken", 400);
    if (req.method === "GET" && url.pathname === "/client") {
      return new Response(options.client, {
        headers: { "content-type": "text/x-python; charset=utf-8" },
      });
    }
    if (url.pathname !== "/run") return text("hostexec: not found", 404);
    if (req.method !== "POST") return text("hostexec: POST /run only", 405);
    if (req.headers.has("content-encoding")) {
      return text("hostexec: the body must not be encoded", 400);
    }
    const length = Number(req.headers.get("content-length"));
    if (!Number.isSafeInteger(length) || length > MAX_BODY) {
      return text(`hostexec: the body must have a Content-Length up to ${MAX_BODY}`, 400);
    }
    // The proxy's mapping comes first, so it wins over a --map of the same
    // container path: it names the workspace this request came from.
    let map = options.map;
    const header = req.headers.get(MAP_HEADER);
    if (header !== null) {
      const m = parseMapping(header);
      if (typeof m === "string") return text(`hostexec: ${MAP_HEADER}: ${m}`, 400);
      map = [m, ...options.map];
    }
    const body = await req.text();
    const exec = parseExecRequest(body, map);
    if (typeof exec === "string") return text(`hostexec: ${exec}`, 400);

    const gone = new AbortController();
    req.signal.addEventListener("abort", () => gone.abort(), { once: true });
    const encoder = new TextEncoder();
    let closed = false;
    let keepalive: ReturnType<typeof setInterval> | undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const send = (e: Event) => {
          if (closed) return;
          try {
            controller.enqueue(encoder.encode(`${JSON.stringify(e)}\n`));
          } catch {
            gone.abort();
          }
        };
        const close = () => {
          if (closed) return;
          closed = true;
          clearInterval(keepalive);
          try {
            controller.close();
          } catch {}
        };
        // Silence on a held request would let a proxy or the client time out.
        keepalive = setInterval(() => send({ event: "keepalive" }), keepaliveMs);
        void (async () => {
          const { held, decision } = options.approvals.hold(exec, gone.signal);
          send({ event: "held", id: held.id });
          const d = await decision;
          if (d !== "approved") {
            if (d !== "gone") send({ event: "refused", reason: d });
            return close();
          }
          send({ event: "started", cwd: exec.cwd });
          const exit = await runOnHost(
            exec,
            options.secrets,
            (s, data) => send({ event: s, data: data.toString("base64") }),
            gone.signal,
          );
          send({ event: "exit", ...exit });
          close();
        })();
      },
      cancel() {
        closed = true;
        clearInterval(keepalive);
        gone.abort();
      },
    });
    return new Response(stream, {
      headers: {
        "content-type": "application/x-ndjson",
        "cache-control": "no-store",
      },
    });
  };
}
