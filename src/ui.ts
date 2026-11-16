// The approval inbox, for a browser or `hostexec review` on the host.
//
// It listens on loopback, which any local process or web page can reach, so
// being reachable grants nothing. Authority is the UI token: every API call
// must carry it in an Authorization header and come from the exact Origin,
// which a cross-site page can do neither of. An exact Host defeats DNS
// rebinding. The API lists what is waiting and decides one request by its
// id, nothing else.
//
// Adapted from strait's `review web` (nix-agent-sandbox/contrib/strait, MIT).

import type { Approvals } from "./approvals.ts";
import { sameSecret } from "./api.ts";
import { INHERITED_ENV } from "./run.ts";

const MAX_BODY = 4096;
const HEADERS = {
  "Cache-Control": "no-store",
  "Content-Security-Policy":
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

export interface Assets {
  html: string;
  js: string;
  css: string;
}

export interface UiOptions {
  /** `127.0.0.1:PORT`, as the browser will send it in Host. */
  authority: () => string;
  token: string;
  approvals: Approvals;
  assets: Assets;
}

const response = (body: string, status = 200, type = "application/json") =>
  new Response(body, {
    status,
    headers: { ...HEADERS, "Content-Type": `${type}; charset=utf-8` },
  });

export function uiHandler(options: UiOptions): (req: Request) => Promise<Response> {
  const { token, approvals, assets } = options;
  return async (req) => {
    const host = options.authority();
    const origin = `http://${host}`;
    const url = new URL(req.url);
    if (req.headers.get("host") !== host || url.search !== "") {
      return response('{"error":"forbidden"}', 403);
    }
    if (req.method === "GET") {
      const asset = (
        {
          "/": [assets.html, "text/html"],
          "/app.js": [assets.js, "text/javascript"],
          "/style.css": [assets.css, "text/css"],
        } as Record<string, [string, string]>
      )[url.pathname];
      return asset ? response(asset[0], 200, asset[1]) : response('{"error":"not found"}', 404);
    }
    // Every API call, listing included, is a POST so that the browser sends
    // Origin. Authorization is not a CORS-safelisted header, and no CORS
    // headers are ever sent, so no other origin can make the call.
    if (
      req.method !== "POST" ||
      req.headers.get("origin") !== origin ||
      !sameSecret(req.headers.get("authorization"), `Bearer ${token}`) ||
      (req.headers.has("sec-fetch-site") && req.headers.get("sec-fetch-site") !== "same-origin")
    ) {
      return response('{"error":"forbidden"}', 403);
    }
    let body: Record<string, unknown>;
    try {
      const raw = await req.text();
      if (raw.length > MAX_BODY) throw new Error("too large");
      const value: unknown = JSON.parse(raw || "{}");
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not an object");
      body = value as Record<string, unknown>;
    } catch {
      return response('{"error":"bad request"}', 400);
    }
    if (url.pathname === "/api/pending") {
      const pending = approvals.list().map((h) => ({
        id: h.id,
        argv: h.exec.argv,
        cwd: h.exec.cwd,
        requestedCwd: h.exec.requestedCwd,
        env: h.exec.env,
        since: h.since,
        expiresAt: h.expiresAt,
      }));
      return response(JSON.stringify({ pending, inheritedEnv: INHERITED_ENV, now: Date.now() }));
    }
    if (url.pathname === "/api/decision") {
      const { id, approve } = body;
      if (typeof id !== "string" || typeof approve !== "boolean") {
        return response('{"error":"bad request"}', 400);
      }
      return approvals.decide(id, approve)
        ? response('{"ok":true}')
        : response('{"error":"no such request: decided, expired, or withdrawn"}', 404);
    }
    return response('{"error":"not found"}', 404);
  };
}
