// A request to run one command, as the container sends it. Everything the
// command gets, argv, working directory and environment, is in the request,
// so the person approving it sees exactly what will run.
//
// Adapted from strait (nix-agent-sandbox/contrib/strait, MIT).

import { isAbsolute, normalize } from "node:path";

export interface ExecRequest {
  argv: string[];
  /** On the host, after the --map translation. */
  cwd: string;
  /** As the container sent it. */
  requestedCwd: string;
  /** Added to the inherited PATH and HOME, overriding them. */
  env: Record<string, string>;
}

/** Container path prefix to host path prefix, both absolute and normalized. */
export type PathMap = readonly (readonly [string, string])[];

export const MAX_BODY = 1024 * 1024;

const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Parse a request body, or return why it is not a valid request. */
export function parseExecRequest(
  text: string,
  map: PathMap = [],
): ExecRequest | string {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return "body is not JSON";
  }
  if (hasDuplicateMember(text)) return "body has a duplicated member";
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return "body is not an object";
  }
  const r = raw as Record<string, unknown>;
  for (const key of Object.keys(r)) {
    if (key !== "argv" && key !== "cwd" && key !== "env") {
      return `body has an unexpected member ${JSON.stringify(key)}`;
    }
  }
  const { argv, cwd, env } = r;
  // A NUL cannot reach exec, so a string holding one is not what would run.
  const text0 = (v: unknown): v is string =>
    typeof v === "string" && !v.includes("\0");
  if (!Array.isArray(argv) || argv.length === 0 || !argv.every(text0)) {
    return "argv must be a non-empty array of strings";
  }
  if (argv[0] === "") return "argv[0] is empty";
  if (!text0(cwd) || !isAbsolute(cwd)) return "cwd must be an absolute path";
  if (env === undefined) {
    // Allowed: no variables beyond PATH and HOME.
  } else if (typeof env !== "object" || env === null || Array.isArray(env)) {
    return "env must be an object";
  }
  const vars: Record<string, string> = {};
  for (const [name, value] of Object.entries(env ?? {})) {
    if (!ENV_NAME.test(name)) {
      return `env name ${JSON.stringify(name)} is not valid`;
    }
    if (!text0(value)) return `env ${name} must be a string`;
    vars[name] = value;
  }
  return { argv, cwd: mapPath(cwd, map), requestedCwd: cwd, env: vars };
}

/**
 * The host path for a container path: the longest matching prefix wins, and
 * a path no prefix covers is kept as it is. `..` is resolved first, so a
 * mapped path cannot climb out of its host directory by its spelling.
 */
export function mapPath(path: string, map: PathMap): string {
  const p = normalize(path);
  let best: readonly [string, string] | undefined;
  for (const entry of map) {
    const [from] = entry;
    const covers = p === from || p.startsWith(from === "/" ? "/" : `${from}/`);
    if (covers && (!best || from.length > best[0].length)) best = entry;
  }
  if (!best) return p;
  const rest = p.slice(best[0].length).replace(/^\/+/, "");
  return rest === "" ? best[1] : `${best[1].replace(/\/+$/, "")}/${rest}`;
}

/** Parse `CONTAINER=HOST`, from --map or the proxy's X-Hostexec-Map. */
export function parseMapping(spec: string): readonly [string, string] | string {
  const eq = spec.indexOf("=");
  if (eq === -1) return `${spec}: expected CONTAINER=HOST`;
  const from = spec.slice(0, eq);
  const to = spec.slice(eq + 1);
  if (!isAbsolute(from) || !isAbsolute(to) || spec.includes("\0")) {
    return `${spec}: both paths must be absolute`;
  }
  const strip = (s: string) => normalize(s).replace(/(.)\/+$/, "$1");
  return [strip(from), strip(to)];
}

/**
 * Whether any object in `text`, which must already be valid JSON, names the
 * same member twice. Validity lets the scan track only strings and brackets.
 */
export function hasDuplicateMember(text: string): boolean {
  const stack: { keys: Set<string> | null; expectKey: boolean }[] = [];
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    const top = stack[stack.length - 1];
    if (c === "{") stack.push({ keys: new Set(), expectKey: true });
    else if (c === "[") stack.push({ keys: null, expectKey: false });
    else if (c === "}" || c === "]") stack.pop();
    else if (c === "," && top?.keys) top.expectKey = true;
    else if (c === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') {
        j += text[j] === "\\" ? 2 : 1;
      }
      if (top?.keys && top.expectKey) {
        const key = JSON.parse(text.slice(i, j + 1)) as string;
        if (top.keys.has(key)) return true;
        top.keys.add(key);
        top.expectKey = false;
      }
      i = j;
    }
  }
  return false;
}
