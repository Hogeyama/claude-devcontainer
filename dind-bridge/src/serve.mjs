import { createHash } from "node:crypto";
import { readlink, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { listen } from "./protocol.mjs";

/** The file serve records its network namespace in, next to its socket. */
export function baseNetnsPath(socket) {
  return `${dirname(socket)}/base-netns`;
}

/**
 * Get serve ready to start: hold a lock for its socket, remove what an
 * earlier serve left behind, and record this namespace for the env file.
 *
 * A container restart kills every process but may keep /run, so stale
 * sockets would make listen fail. The lock is an abstract socket: it belongs
 * to the namespace and vanishes with its owner, so a crash never leaves it.
 * Holding it proves that no live serve uses the files about to be removed.
 */
export async function prepareServe(socket) {
  const id = createHash("sha256")
    .update(resolve(socket))
    .digest("hex")
    .slice(0, 32);
  const lock = createServer((client) => client.destroy());
  try {
    await listen(lock, `\0dind-bridge-serve-${id}`);
  } catch (error) {
    if (error.code === "EADDRINUSE")
      throw new Error(`another serve is running for ${socket}`);
    throw error;
  }
  lock.unref();
  for (const path of [socket, `${socket}.api`]) await rm(path, { force: true });
  await writeFile(
    baseNetnsPath(socket),
    `${await readlink("/proc/self/ns/net")}\n`,
    { mode: 0o644 },
  );
  return lock;
}
