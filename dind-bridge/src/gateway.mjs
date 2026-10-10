import { chmod, unlink } from "node:fs/promises";
import { createServer } from "node:net";
import {
  closeServer,
  listen,
  openSocket,
  pipeSockets,
  readFrame,
  trackServer,
  writeFrame,
} from "./protocol.mjs";

function validPort(value) {
  return Number.isInteger(value) && value > 0 && value <= 65535;
}

/**
 * The gateway runs in the namespace that can reach DinD and is reached only
 * through Unix sockets. It offers exactly two things:
 *
 * - `${socketPath}.api`: the Docker API at `dockerSocket`, byte for byte.
 * - `socketPath`: a TCP connection to `publishHost:<port>`.
 *
 * `publishHost` is the DinD container's address, which must listen on nothing
 * but published ports. Fixing the address is the whole authorization: no
 * request can steer a connection to another service, and no Docker state has
 * to be tracked to prove where a port came from.
 */
export async function startGateway({ socketPath, dockerSocket, publishHost }) {
  const sockets = new Set();
  const track = (socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  };
  const api = createServer({ allowHalfOpen: true }, (client) => {
    void openSocket({ path: dockerSocket }).then(
      (docker) => {
        track(docker);
        if (client.destroyed) docker.destroy();
        else pipeSockets(client, docker);
      },
      () => client.destroy(),
    );
  });
  trackServer(api, sockets);
  const control = createServer({ allowHalfOpen: true }, (client) => {
    void (async () => {
      let target;
      try {
        const frame = await readFrame(client, 8192);
        if (frame.kind !== "connect" || !validPort(frame.port))
          throw new Error("unknown bridge operation");
        target = await openSocket({ host: publishHost, port: frame.port });
        track(target);
        if (client.destroyed) {
          target.destroy();
          return;
        }
        writeFrame(client, { ok: true });
        pipeSockets(client, target);
      } catch (error) {
        target?.destroy();
        if (!client.destroyed) {
          writeFrame(client, { ok: false, error: error.message });
          client.end();
          setTimeout(() => client.destroy(), 1000).unref();
        }
      }
    })();
  });
  trackServer(control, sockets);
  const servers = [];
  const close = async () => {
    for (const socket of sockets) socket.destroy();
    for (const [server, path] of servers) {
      await closeServer(server);
      await unlink(path).catch(() => {});
    }
  };
  try {
    for (const [server, path] of [
      [api, `${socketPath}.api`],
      [control, socketPath],
    ]) {
      await listen(server, path);
      servers.push([server, path]);
      await chmod(path, 0o600);
    }
  } catch (error) {
    await close();
    throw error;
  }
  return { close };
}
