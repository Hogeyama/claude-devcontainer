import { createServer as createHttpServer, request } from "node:http";
import { createServer } from "node:net";
import { waitForDocker } from "./docker_ready.mjs";
import {
  closeServer,
  listen,
  openSocket,
  pipeSockets,
  splitUpgradeBody,
  trackServer,
} from "./protocol.mjs";

const BIND_HOST = "127.0.0.1";
const POLL_MS = 1000;
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
 * Serve the Docker API at `dockerSocket` on the loopback address `api`, and
 * mirror on 127.0.0.1 every port dockerd published on `publishIp`. A mirrored
 * port connects only to the same port of `publishHost`, the DinD container's
 * address, which must listen on nothing but published ports. Fixing the
 * address is the whole authorization: no request can steer a connection to
 * another service.
 *
 * The first API request waits for Docker to be ready and an initial port
 * snapshot, so the relay can listen before dockerd is up. A start or restart
 * response is held until the ports it created are mirrored, so a client never
 * sees a mapped port it cannot connect to yet. Everything else streams
 * straight through.
 */
export async function startRelay({
  dockerSocket,
  api,
  publishHost,
  publishIp,
  onWarning = () => {},
}) {
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
      const containers = await dockerJson(dockerSocket, "/containers/json");
      lastSyncError = undefined;
      const desired = new Set();
      published = [];
      for (const container of Array.isArray(containers) ? containers : []) {
        const ports = (container.Ports ?? [])
          .filter(
            (port) =>
              port.IP === publishIp &&
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
          void openSocket({ host: publishHost, port }).then(
            (upstream) => {
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
        `the container started, but its published TCP port ${taken.join(", ")} is already in use in the agent and is not forwarded`,
      );
  };

  let apiReady;
  const prepareApi = () => {
    apiReady ??= (async () => {
      await waitForDocker(dockerSocket);
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
    let upstreamSocket;
    const raw = head !== undefined;
    const report = (error) => {
      upstream?.destroy();
      upstreamSocket?.destroy();
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
        socketPath: dockerSocket,
        method: req.method,
        path: req.url,
        headers: requestHeaders(req),
        agent: false,
      });
      upstream.on("socket", (socket) => {
        upstreamSocket = socket;
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
            response.pipe(upstreamSocket);
            if (rest.length) upstreamSocket.write(rest);
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
  return { close };
}
