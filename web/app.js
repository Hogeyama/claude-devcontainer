// This module deliberately uses textContent, native buttons, and no
// dependencies. Everything in a held request comes from the container and is
// hostile: arguments, working directory, environment.
//
// Escaping adapted from strait's web UI (nix-agent-sandbox/contrib/strait, MIT).

const INVISIBLE = /[\p{Cc}\p{Cf}\uD800-\uDFFF\u2028\u2029]/u;

const escapeChar = (character) => `\\u{${character.codePointAt(0).toString(16)}}`;

/** Text with control and format characters, and backslashes, escaped. */
export function visible(value) {
  return value.replace(/[\\\p{Cc}\p{Cf}\uD800-\uDFFF\u2028\u2029]/gu, (character) =>
    character === "\\" ? "\\\\" : escapeChar(character),
  );
}

// One argument as a shell word, the way a person would type it. Nothing is
// special inside '...', so only an argument with a quote or an invisible
// character needs $'...', where every backslash starts an escape.
export function shellWord(arg) {
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(arg)) return arg;
  if (!arg.includes("'") && !INVISIBLE.test(arg)) return `'${arg}'`;
  let text = "";
  for (const character of arg) {
    const code = character.codePointAt(0);
    text +=
      character === "\\" || character === "'"
        ? `\\${character}`
        : character === "\n"
          ? "\\n"
          : character === "\t"
            ? "\\t"
            : character === "\r"
              ? "\\r"
              : !INVISIBLE.test(character)
                ? character
                : code < 0x100
                  ? `\\x${code.toString(16).padStart(2, "0")}`
                  : code < 0x10000
                    ? `\\u${code.toString(16).padStart(4, "0")}`
                    : `\\U${code.toString(16).padStart(8, "0")}`;
  }
  return `$'${text}'`;
}

export const commandLine = (argv) => argv.map(shellWord).join(" ");

export function takeToken(location, history) {
  const fragment = location.hash;
  // Before any request or rendering. Never kept in storage.
  try {
    history.replaceState(null, "", location.pathname);
  } catch {
    return "";
  }
  return /^#[0-9a-f]{64}$/.test(fragment) ? fragment.slice(1) : "";
}

const isStrings = (v) => Array.isArray(v) && v.every((x) => typeof x === "string");

/** A record from the server, or null if it is not one in full. */
export function record(value) {
  if (!value || typeof value !== "object") return null;
  const { id, argv, cwd, requestedCwd, env, since, expiresAt } = value;
  if (
    typeof id !== "string" ||
    !isStrings(argv) ||
    argv.length === 0 ||
    typeof cwd !== "string" ||
    typeof requestedCwd !== "string" ||
    !env ||
    typeof env !== "object" ||
    !Object.values(env).every((v) => typeof v === "string") ||
    typeof since !== "number" ||
    typeof expiresAt !== "number"
  ) {
    return null;
  }
  return { id, argv, cwd, requestedCwd, env, since, expiresAt };
}

function main() {
  const token = takeToken(window.location, window.history);
  const list = document.getElementById("pending");
  const status = document.getElementById("status");
  if (!token) {
    status.textContent = "No token in the link. Open the link `hostexec serve` printed.";
    return;
  }
  const api = async (path, body) => {
    const res = await fetch(path, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: res.ok, data: await res.json().catch(() => ({})) };
  };
  // Cards stay as they are while their request waits, so a click lands on
  // what was read; ids are random and a request never changes.
  const cards = new Map();
  let skew = 0;
  let seen = 0;

  const field = (label, text) => {
    const row = document.createElement("div");
    row.className = "field";
    const dt = document.createElement("span");
    dt.className = "label";
    dt.textContent = label;
    const dd = document.createElement("pre");
    dd.textContent = text;
    row.append(dt, dd);
    return row;
  };

  const card = (r, inherited) => {
    const el = document.createElement("article");
    el.className = "card";
    el.append(field("Command", commandLine(r.argv)));
    const cwd = r.cwd === r.requestedCwd ? visible(r.cwd) : `${visible(r.cwd)}\n(from ${visible(r.requestedCwd)} in the container)`;
    el.append(field("Directory", cwd));
    const env = Object.entries(r.env);
    el.append(
      field(
        "Environment",
        [
          `${inherited.map(visible).join(", ")} from the host${env.length ? ", plus:" : ", nothing else"}`,
          ...env.map(([k, v]) => `${visible(k)}=${shellWord(v)}`),
        ].join("\n"),
      ),
    );
    const meta = document.createElement("p");
    meta.className = "meta";
    el.append(meta);
    const buttons = document.createElement("div");
    buttons.className = "buttons";
    const decide = (approve) => async () => {
      for (const b of buttons.querySelectorAll("button")) b.disabled = true;
      const { ok, data } = await api("/api/decision", { id: r.id, approve });
      meta.textContent = ok ? (approve ? "Approved." : "Denied.") : String(data.error ?? "Failed.");
      el.classList.add("done");
      setTimeout(() => {
        el.remove();
        cards.delete(r.id);
      }, 1500);
    };
    const approve = document.createElement("button");
    approve.className = "approve";
    approve.textContent = "Run on the host";
    approve.addEventListener("click", decide(true));
    const deny = document.createElement("button");
    deny.className = "deny";
    deny.textContent = "Deny";
    deny.addEventListener("click", decide(false));
    buttons.append(approve, deny);
    el.append(buttons);
    return { el, meta, r };
  };

  const tick = () => {
    for (const { el, meta, r } of cards.values()) {
      if (el.classList.contains("done")) continue;
      const left = Math.max(0, Math.round((r.expiresAt - (Date.now() + skew)) / 1000));
      meta.textContent = `Asked ${new Date(r.since).toLocaleTimeString()}, denied by itself in ${left}s`;
    }
  };

  const poll = async () => {
    try {
      const { ok, data } = await api("/api/pending", {});
      if (!ok) {
        status.textContent = `The server refused: ${String(data.error ?? "unknown")}. Reopen the link.`;
        return;
      }
      skew = data.now - Date.now();
      const inherited = isStrings(data.inheritedEnv) ? data.inheritedEnv : [];
      const live = new Set();
      for (const raw of data.pending ?? []) {
        const r = record(raw);
        // A request shown without all of its command must never be approvable.
        if (!r) continue;
        live.add(r.id);
        if (!cards.has(r.id)) {
          const c = card(r, inherited);
          cards.set(r.id, c);
          list.prepend(c.el);
          if (r.since > seen && document.hidden && "Notification" in window && Notification.permission === "granted") {
            new Notification("hostexec", { body: commandLine(r.argv).slice(0, 200) });
          }
          seen = Math.max(seen, r.since);
        }
      }
      for (const [id, c] of cards) {
        if (!live.has(id) && !c.el.classList.contains("done")) {
          c.el.remove();
          cards.delete(id);
        }
      }
      status.textContent = cards.size ? "" : "Nothing is waiting.";
      tick();
    } catch {
      status.textContent = "The server is not answering. Is `hostexec serve` running?";
    }
  };
  document.getElementById("notify").addEventListener("click", () => {
    if ("Notification" in window) void Notification.requestPermission();
  });
  void poll();
  setInterval(poll, 1000);
}

if (typeof window !== "undefined") main();
