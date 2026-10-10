#!/usr/bin/env node
// Relay the DinD Docker API and published TCP ports to the agent's loopback.
// See ../README.md.
import { appendFile } from "node:fs/promises";
import { waitForDocker } from "./docker_ready.mjs";
import { loopbackEndpoint } from "./protocol.mjs";
import { startRelay } from "./relay.mjs";

const USAGE = `usage: dind-bridge --docker-host unix:///PATH --publish-host HOST
                   --api tcp://127.0.0.1:PORT [--publish-ip IP] [--log PATH]
`;

const REQUIRED = ["docker-host", "publish-host", "api"];
const OPTIONAL = ["publish-ip", "log"];

/** Parse `--key value` pairs, rejecting anything not taken. */
function parseOptions(args) {
  const opts = {};
  const rest = [...args];
  while (rest.length) {
    const key = rest.shift();
    if (!key?.startsWith("--") || !rest.length)
      throw new Error("invalid arguments");
    const name = key.slice(2);
    if (!REQUIRED.includes(name) && !OPTIONAL.includes(name))
      throw new Error(`unknown option ${key}`);
    if (Object.hasOwn(opts, name)) throw new Error(`${key} given twice`);
    opts[name] = rest.shift();
  }
  for (const name of REQUIRED)
    if (!opts[name]) throw new Error(`--${name} is required`);
  opts.api = loopbackEndpoint(opts.api, "--api");
  return opts;
}

function unixSocketPath(value, what) {
  const path = value.startsWith("unix://") ? value.slice("unix://".length) : "";
  if (!path.startsWith("/") || path.includes("\0"))
    throw new Error(`${what} must be unix:///ABSOLUTE/PATH`);
  return path;
}

async function main(args) {
  if (!args.length || args[0] === "--help" || args[0] === "-h") {
    process.stdout.write(USAGE);
    if (!args.length) process.exitCode = 2;
    return;
  }
  const opts = parseOptions(args);
  const dockerSocket = unixSocketPath(opts["docker-host"], "--docker-host");
  const relay = await startRelay({
    dockerSocket,
    api: opts.api,
    publishHost: opts["publish-host"],
    publishIp: opts["publish-ip"] ?? opts["publish-host"],
    beforeFirstRequest: () => waitForDocker(dockerSocket),
    onWarning: (error) => {
      const line = `${new Date().toISOString()} ${error.message}\n`;
      if (opts.log) void appendFile(opts.log, line).catch(() => {});
      else process.stderr.write(line);
    },
  });
  const stop = async () => {
    await relay.close();
    process.exit(0);
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
  process.stdout.write("ready\n");
}

main(process.argv.slice(2)).catch((error) => {
  console.error(`dind-bridge: ${error.message}`);
  process.exitCode = 1;
});
