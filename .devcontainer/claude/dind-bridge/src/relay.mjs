import { spawn } from "node:child_process";
import { readdir, readlink } from "node:fs/promises";
import { createServer as createHttpServer, request } from "node:http";
import { createServer } from "node:net";
import {
  closeServer,
  gatewayRequest,
  listen,
  pipeSockets,
  splitUpgradeBody,
  trackServer,
  writeFrame,
} from "./protocol.mjs";

const BIND_HOST = "127.0.0.1";
const POLL_MS = 1000;
const IDLE_CHECK_MS = 30_000;
const START =
  /^\/(?:v[0-9.]+\/)?containers\/([^/?]+)\/(?:start|restart)(?:\?|$)/;

function rawHeaders(res) {
  let value = `HTTP/1.1 ${res.statusCode} ${res.statusMessage}\r\n`;
  for (let i = 0; i < res.rawHeaders.length; i += 2)
    value += `${res.rawHeaders[i]}: ${res.rawHeaders[i + 1]}\r\n`;
  return `${value}\r\n`;
}

// `req.headers` joins a repeated header into one comma-separated value, but
// Docker reads some headers line by line, such as the session methods
// BuildKit announces. Only a repeated header becomes an array: http.request
// rejects an array for Host.
function requestHeaders(req) {
  return Object.fromEntries(
    Object.entries(req.headersDistinct).map(([name, values]) => [
      name,
      values.length === 1 ? values[0] : values,
    ]),
  );
}

function dockerJson(socketPath, path) {
  return new Promise((resolve, reject) => {
    const req = request({ socketPath, path, agent: false }, (res) => {
      const chunks = [];
      let size = 0;
      res.on("data", (chunk) => {
        size += chunk.length;
        if (size > 8 * 1024 * 1024)
          res.destroy(new Error("Docker metadata exceeds limit"));
        else chunks.push(chunk);
      });
      res.on("error", reject);
      res.on("end", () => {
        if (res.statusCode !== 200)
          return reject(new Error(`Docker ${path}: HTTP ${res.statusCode}`));
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString()));
        } catch (error) {
          reject(error);
        }
      });
    });
    req.setTimeout(10_000, () => req.destroy(new Error("Docker timed out")));
    req.on("error", reject);
    req.end();
  });
}

/**
 * Inside a network namespace, serve the gateway's Docker API on the loopback
 * address `api`, and mirror every port dockerd published on `publishHost` on
 * 127.0.0.1. Each mirrored connection goes through the gateway, which can
 * only reach its own publish host: `publishHost` here is only the address
 * dockerd reports for a port.
 *
 * The first API request waits for `beforeFirstRequest` and an initial port
 * snapshot, so the relay can listen at once without delaying every Bash
 * command. A start or restart response is held until the ports it created
 * are mirrored, so a client never sees a mapped port it cannot connect to
 * yet. Everything else streams straight through.
 */
export async function startRelay({
  socketPath,
  api,
  publishHost,
  beforeFirstRequest,
  onWarning = () => {},
}) {
  const dockerApi = `${socketPath}.api`;
  const sockets = new Set();
  const listeners = new Map();
  const conflicts = new Set();
  // Each running container's mirrored ports, as of the latest poll.
  let published = [];
  let closed = false;
  let syncing;
  let lastSyncError;
  const sync = () => {
    if (syncing) return syncing;
    syncing = (async () => {
      const containers = await dockerJson(dockerApi, "/containers/json");
      lastSyncError = undefined;
      const desired = new Set();
      published = [];
      for (const container of Array.isArray(containers) ? containers : []) {
        const ports = (container.Ports ?? [])
          .filter(
            (port) =>
              port.IP === publishHost &&
              port.Type === "tcp" &&
              Number.isInteger(port.PublicPort),
          )
          .map((port) => port.PublicPort);
        for (const port of ports) desired.add(port);
        published.push({
          id: String(container.Id ?? ""),
          names: (container.Names ?? []).map((name) =>
            String(name).replace(/^\//, ""),
          ),
          ports,
        });
      }
      for (const [port, entry] of listeners) {
        if (desired.has(port)) continue;
        for (const client of entry.clients) client.destroy();
        listeners.delete(port);
        await closeServer(entry.server);
      }
      for (const port of conflicts)
        if (!desired.has(port)) conflicts.delete(port);
      for (const port of desired) {
        if (listeners.has(port) || closed) continue;
        const clients = new Set();
        const server = createServer({ allowHalfOpen: true }, (client) => {
          client.pause();
          void gatewayRequest(socketPath, { kind: "connect", port }).then(
            ({ socket: upstream }) => {
              clients.add(upstream);
              upstream.once("close", () => clients.delete(upstream));
              if (client.destroyed || closed) upstream.destroy();
              else pipeSockets(client, upstream);
            },
            () => client.destroy(),
          );
        });
        trackServer(server, clients);
        try {
          await listen(server, { host: BIND_HOST, port });
        } catch (error) {
          // Leave it unmirrored and retry on the next poll. A client may
          // still reach whatever holds the port, so a start that created it
          // reports the conflict instead of success.
          if (!conflicts.has(port))
            onWarning(
              new Error(
                `cannot mirror Docker TCP port ${port}: ${error.message}`,
              ),
            );
          conflicts.add(port);
          continue;
        }
        conflicts.delete(port);
        listeners.set(port, { server, clients });
      }
    })()
      .catch((error) => {
        if (error.message !== lastSyncError?.message) onWarning(error);
        lastSyncError = error;
        throw error;
      })
      .finally(() => {
        syncing = undefined;
      });
    return syncing;
  };
  // Mirror a started container's ports, or say why they are not. The sync
  // must not be a poll that began before the start, and unlike a poll its
  // failure reaches the client: the ports may be unmirrored.
  const mirrorStarted = async (reference) => {
    await syncing?.catch(() => {});
    await sync();
    const matches = published.filter(
      (entry) =>
        entry.names.includes(reference) || entry.id.startsWith(reference),
    );
    const container = matches.length === 1 ? matches[0] : undefined;
    const taken = container?.ports.filter((port) => conflicts.has(port));
    if (taken?.length)
      throw new Error(
        `the container started, but its published TCP port ${taken.join(", ")} is already in use in this network namespace and is not forwarded`,
      );
  };

  let apiReady;
  const prepareApi = () => {
    apiReady ??= (async () => {
      await beforeFirstRequest();
      await syncing?.catch(() => {});
      await sync();
    })().catch((error) => {
      apiReady = undefined;
      throw error;
    });
    return apiReady;
  };
  const proxy = async (req, response, head) => {
    let upstream;
    let dockerSocket;
    const raw = head !== undefined;
    const report = (error) => {
      upstream?.destroy();
      dockerSocket?.destroy();
      if (raw) response.destroy();
      else if (!response.headersSent && !response.destroyed) {
        response.writeHead(502, {
          "content-type": "text/plain",
          connection: "close",
        });
        response.end(`dind-bridge: ${error.message}\n`);
      } else response.destroy();
    };
    try {
      const { body, rest } = raw
        ? await splitUpgradeBody(req, response, head)
        : {};
      if (response.destroyed || closed) return;
      await prepareApi();
      if (response.destroyed || closed) return;
      const started =
        req.method === "POST" ? START.exec(req.url)?.[1] : undefined;
      upstream = request({
        socketPath: dockerApi,
        method: req.method,
        path: req.url,
        headers: requestHeaders(req),
        agent: false,
      });
      upstream.on("socket", (socket) => {
        dockerSocket = socket;
        sockets.add(socket);
        socket.once("close", () => sockets.delete(socket));
      });
      upstream.on("error", report);
      upstream.on("response", (res) => {
        res.pause();
        res.on("error", report);
        const ready =
          started !== undefined && res.statusCode >= 200 && res.statusCode < 300
            ? mirrorStarted(decodeURIComponent(started))
            : Promise.resolve();
        void ready.then(() => {
          if (response.destroyed) {
            res.destroy();
            return;
          }
          if (raw) {
            response.write(rawHeaders(res));
            // Docker's legacy attach responds 200 and then hijacks the socket.
            res.pipe(response);
            response.pipe(dockerSocket);
            if (rest.length) dockerSocket.write(rest);
          } else {
            response.writeHead(res.statusCode, res.rawHeaders);
            // Docker flushes headers before a long-poll body (container
            // wait); the CLI waits for them before starting the container.
            response.flushHeaders();
            res.pipe(response);
          }
          res.resume();
        }, report);
      });
      upstream.on("upgrade", (res, socket, upstreamHead) => {
        if (!raw) {
          socket.destroy();
          report(new Error("unexpected Docker HTTP upgrade"));
          return;
        }
        response.write(rawHeaders(res));
        if (upstreamHead.length) response.write(upstreamHead);
        if (rest.length) socket.write(rest);
        pipeSockets(response, socket);
      });
      req.on("aborted", () => upstream.destroy());
      response.on("close", () => {
        if (raw || !response.writableFinished) upstream.destroy();
      });
      if (raw) upstream.end(body);
      else req.pipe(upstream);
    } catch (error) {
      report(error);
    }
  };
  const server = createHttpServer((req, res) => void proxy(req, res));
  server.on("upgrade", (req, socket, head) => void proxy(req, socket, head));
  server.on("clientError", (_error, socket) => socket.destroy());
  trackServer(server, sockets);
  let timer;
  const close = async () => {
    if (closed) return;
    closed = true;
    clearInterval(timer);
    await syncing?.catch(() => {});
    for (const socket of sockets) socket.destroy();
    for (const entry of listeners.values()) {
      for (const client of entry.clients) client.destroy();
      await closeServer(entry.server);
    }
    listeners.clear();
    await closeServer(server);
  };
  try {
    // The API takes its address before any port is mirrored, so a container
    // publishing the same port is the one reported as a conflict.
    await listen(server, api);
    void sync().catch(() => {});
    timer = setInterval(() => void sync().catch(() => {}), POLL_MS);
  } catch (error) {
    await close();
    throw error;
  }
  return {
    close,
    /** No open API or mirrored connection. */
    idle() {
      let open = sockets.size;
      for (const entry of listeners.values()) open += entry.clients.size;
      return open === 0;
    },
  };
}

/**
 * The abstract socket names of one namespace's relay. They belong to the
 * network namespace and vanish with their owner, so `name` both names the
 * live relay and, with `lock`, keeps a second one from starting. The env file
 * derives the same name without starting a process.
 */
export function relayNames(instance, namespace, prefix) {
  const id = /^net:\[([0-9]+)\]$/.exec(namespace)?.[1];
  if (
    !id ||
    !/^[A-Za-z0-9_-]{1,32}$/.test(instance) ||
    !/^[A-Za-z0-9_-]{1,32}$/.test(prefix)
  )
    throw new Error("invalid DinD relay identity");
  const base = `${prefix}-${process.getuid()}-${instance}-${id}`;
  return { name: `\0${base}`, lock: `\0${base}-lock` };
}

/**
 * Whether no other process shares this network namespace. A sandbox in its
 * own PID namespace may show another namespace's /proc; there the answer is
 * unknowable, and the sandbox's PID namespace takes the relay with it anyway.
 */
async function namespaceAlone(namespace) {
  try {
    if ((await readlink("/proc/self")) !== String(process.pid)) return false;
    for (const entry of await readdir("/proc")) {
      if (!/^[0-9]+$/.test(entry) || Number(entry) === process.pid) continue;
      try {
        if ((await readlink(`/proc/${entry}/ns/net`)) === namespace)
          return false;
      } catch {}
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Run this namespace's relay until no other process is left in the namespace.
 *
 * The lock name keeps a second relay from starting; the public name, which
 * the env file looks for, appears only once the API is listening, so no Bash
 * is pointed at a relay that cannot answer yet. The relay touches no file:
 * a sandbox may allow no writes. Its warnings are dropped; a port it cannot
 * mirror still reaches the client as the start response's error.
 */
export async function runRelay({
  socketPath,
  instance,
  namePrefix,
  api,
  publishHost,
  beforeFirstRequest,
}) {
  const namespace = await readlink("/proc/self/ns/net");
  const { name, lock } = relayNames(instance, namespace, namePrefix);
  const lockServer = createServer((client) => client.destroy());
  try {
    await listen(lockServer, lock);
  } catch (error) {
    if (error.code === "EADDRINUSE") return { stopped: Promise.resolve() };
    throw error;
  }
  const control = createServer((client) => {
    client.on("error", () => {});
    writeFrame(client, {
      ok: true,
      api: `tcp://${api.host}:${api.port}`,
      pid: process.pid,
    });
    client.end();
  });
  const controlSockets = new Set();
  trackServer(control, controlSockets);
  let relay;
  try {
    relay = await startRelay({
      socketPath,
      api,
      publishHost,
      beforeFirstRequest,
    });
    await listen(control, name);
  } catch (error) {
    await relay?.close();
    await closeServer(lockServer);
    throw error;
  }
  let timer;
  let finish;
  const stopped = new Promise((resolve) => {
    finish = resolve;
  });
  let stopping;
  const stop = () => {
    stopping ??= (async () => {
      clearInterval(timer);
      // Withdraw the public name first so no new Bash is pointed here.
      for (const socket of controlSockets) socket.destroy();
      await closeServer(control);
      await relay.close();
      await closeServer(lockServer);
      finish();
    })();
    return stopping;
  };
  for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"])
    process.once(signal, () => void stop());
  timer = setInterval(() => {
    void (async () => {
      if (relay.idle() && (await namespaceAlone(namespace))) await stop();
    })();
  }, IDLE_CHECK_MS);
  return { stopped };
}

/**
 * Return the API address of this namespace's relay, starting it if needed:
 * `relayArgs` follow `script` to run one.
 */
export async function ensureRelay({ instance, namePrefix, script, relayArgs }) {
  const namespace = await readlink("/proc/self/ns/net");
  const { name } = relayNames(instance, namespace, namePrefix);
  const probe = async () => {
    try {
      const { socket, response } = await gatewayRequest(name, {
        kind: "status",
      });
      socket.destroy();
      return response.api;
    } catch {
      return undefined;
    }
  };
  let api = await probe();
  if (api) return api;
  // Detached from the caller's session and output, so the relay neither holds
  // the command's pipes open nor dies with its process group.
  const relay = spawn(process.execPath, [script, ...relayArgs], {
    detached: true,
    stdio: ["ignore", "ignore", "pipe"],
  });
  let failed;
  let stderr = "";
  relay.stderr.on("data", (chunk) => {
    if (stderr.length < 4096) stderr += chunk;
  });
  // A relay that lost the race for the name exits 0; keep waiting for the
  // winner. Any other exit means this namespace cannot get one now.
  relay.once("exit", (code) => {
    if (code !== 0) failed = stderr.trim() || `exit ${code}`;
  });
  try {
    for (let attempt = 0; attempt < 100 && !api && !failed; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      api = await probe();
    }
  } finally {
    relay.stderr.destroy();
    relay.unref();
  }
  if (!api)
    throw new Error(
      `namespace relay did not start${failed ? `: ${failed}` : ""}`,
    );
  return api;
}
