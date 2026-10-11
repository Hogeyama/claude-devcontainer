import { connect } from "node:net";

const TIMEOUT = 10_000;
const MAX_BODY = 1024 * 1024;

export function openSocket(options) {
  return new Promise((resolve, reject) => {
    const socket = connect({ ...options, allowHalfOpen: true });
    const timer = setTimeout(
      () => socket.destroy(new Error("bridge connection timed out")),
      TIMEOUT,
    );
    socket.once("error", reject);
    socket.once("connect", () => {
      clearTimeout(timer);
      socket.off("error", reject);
      // Keep asynchronous errors observed while ownership moves to a caller.
      socket.on("error", () => {});
      resolve(socket);
    });
    socket.once("close", () => clearTimeout(timer));
  });
}

// An upgrade request's body (exec start sends JSON) must go upstream with
// the request: Docker answers only after reading all of it, and the rest is
// the hijacked stream. Node leaves the body in `head` and on the socket.
export async function splitUpgradeBody(req, socket, head) {
  const length = Number(req.headers["content-length"] ?? 0);
  if (!Number.isSafeInteger(length) || length < 0 || length > MAX_BODY)
    throw new Error("invalid upgrade request body");
  let buffered = head;
  if (buffered.length < length)
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => done(new Error("upgrade request body timed out")),
        TIMEOUT,
      );
      const done = (error) => {
        clearTimeout(timer);
        socket.off("data", data);
        socket.off("error", done);
        socket.off("end", ended);
        socket.pause();
        if (error) reject(error);
        else resolve();
      };
      const data = (chunk) => {
        buffered = Buffer.concat([buffered, chunk]);
        if (buffered.length >= length) done();
      };
      const ended = () => done(new Error("upgrade request body truncated"));
      socket.on("data", data);
      socket.once("error", done);
      socket.once("end", ended);
      socket.resume();
    });
  return {
    body: buffered.subarray(0, length),
    rest: buffered.subarray(length),
  };
}

// A half-close is an ordinary state, not a sign of a dead peer: the Docker
// CLI closes its write side as soon as it has no stdin, and the container
// keeps writing until it exits. Only an error or an abrupt close ends both.
export function pipeSockets(a, b) {
  const destroy = () => {
    a.destroy();
    b.destroy();
  };
  for (const socket of [a, b]) {
    socket.on("error", destroy);
    socket.on("close", () => {
      if (!socket.readableEnded || !socket.writableFinished) destroy();
    });
  }
  a.pipe(b);
  b.pipe(a);
  a.resume();
  b.resume();
}

export function listen(server, options) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(options, () => {
      server.off("error", reject);
      resolve();
    });
  });
}

export function trackServer(server, sockets) {
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("error", () => {});
    socket.once("close", () => sockets.delete(socket));
  });
}

export function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}
