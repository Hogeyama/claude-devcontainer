#!/usr/bin/env node
// Relay the DinD Docker API and published TCP ports into the network
// namespaces of Claude Code's Bash sandbox. See ../README.md.
import { realpathSync } from "node:fs";
import { appendFile } from "node:fs/promises";
import { dirname } from "node:path";
import { waitForDocker } from "./docker_ready.mjs";
import { renderEnvFile } from "./env_file.mjs";
import { startGateway } from "./gateway.mjs";
import { loopbackEndpoint } from "./protocol.mjs";
import { ensureRelay, runRelay, startRelay } from "./relay.mjs";
import { baseNetnsPath, prepareServe } from "./serve.mjs";

const USAGE = `usage: dind-bridge <command> [options]

  serve     --socket PATH --docker-host unix:///PATH --publish-host HOST
            --api tcp://127.0.0.1:PORT [--publish-ip IP]
  env-file  --socket PATH --api tcp://127.0.0.1:PORT --publish-ip IP
            [--instance NAME] [--name-prefix NAME] [--node PATH] [--script PATH]
  ensure    --socket PATH --api tcp://127.0.0.1:PORT --publish-ip IP
            [--instance NAME] [--name-prefix NAME]
  relay     (started by ensure)
`;

const RELAY = {
  required: ["socket", "api", "publish-ip"],
  optional: ["instance", "name-prefix"],
};
const COMMANDS = {
  serve: {
    required: ["socket", "docker-host", "publish-host", "api"],
    optional: ["publish-ip"],
  },
  "env-file": {
    required: RELAY.required,
    optional: [...RELAY.optional, "node", "script"],
  },
  ensure: RELAY,
  relay: RELAY,
};
const DEFAULTS = { instance: "default", "name-prefix": "dind-bridge" };

/** Parse `--key value` pairs for a command, rejecting anything it does not take. */
function parseOptions(command, args) {
  const spec = COMMANDS[command];
  if (!spec) throw new Error(`unknown command ${command}`);
  const opts = {};
  const rest = [...args];
  while (rest.length) {
    const key = rest.shift();
    if (!key?.startsWith("--") || !rest.length)
      throw new Error(`invalid ${command} arguments`);
    const name = key.slice(2);
    if (!spec.required.includes(name) && !spec.optional.includes(name))
      throw new Error(`unknown option ${key} for ${command}`);
    if (Object.hasOwn(opts, name)) throw new Error(`${key} given twice`);
    opts[name] = rest.shift();
  }
  for (const name of spec.required)
    if (!opts[name]) throw new Error(`--${name} is required`);
  for (const name of spec.optional)
    if (!Object.hasOwn(opts, name) && Object.hasOwn(DEFAULTS, name))
      opts[name] = DEFAULTS[name];
  // Validate --api here too, so env-file never writes a bad one into a file.
  opts.api = loopbackEndpoint(opts.api, "--api");
  return opts;
}

function unixSocketPath(value, what) {
  const path = value.startsWith("unix://") ? value.slice("unix://".length) : "";
  if (!path.startsWith("/") || path.includes("\0"))
    throw new Error(`${what} must be unix:///ABSOLUTE/PATH`);
  return path;
}

/**
 * Start the gateway, and a relay in its own namespace so that clients there
 * see the API and published ports on 127.0.0.1 like everywhere else.
 */
async function serve(opts) {
  const dockerSocket = unixSocketPath(opts["docker-host"], "--docker-host");
  await prepareServe(opts.socket);
  const gateway = await startGateway({
    socketPath: opts.socket,
    dockerSocket,
    publishHost: opts["publish-host"],
  });
  const directory = dirname(opts.socket);
  let relay;
  try {
    relay = await startRelay({
      socketPath: opts.socket,
      api: opts.api,
      publishHost: opts["publish-ip"] ?? opts["publish-host"],
      beforeFirstRequest: () => waitForDocker(`${opts.socket}.api`),
      onWarning: (error) =>
        void appendFile(
          `${directory}/relay.log`,
          `${new Date().toISOString()} ${error.message}\n`,
        ).catch(() => {}),
    });
  } catch (error) {
    await gateway.close();
    throw error;
  }
  const stop = async () => {
    await relay.close();
    await gateway.close();
    process.exit(0);
  };
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
  process.stdout.write("ready\n");
}

async function main(args) {
  const [command, ...rest] = args;
  if (command === undefined || command === "--help" || command === "-h") {
    process.stdout.write(USAGE);
    if (command === undefined) process.exitCode = 2;
    return;
  }
  const opts = parseOptions(command, rest);
  const api = `tcp://${opts.api.host}:${opts.api.port}`;
  const relayOptions = {
    socketPath: opts.socket,
    instance: opts.instance,
    namePrefix: opts["name-prefix"],
    api: opts.api,
    publishHost: opts["publish-ip"],
    // Probe the raw gateway API: this also tolerates serve still starting.
    // The relay's listener (and an ordinary Bash command) never waits here.
    beforeFirstRequest: () => waitForDocker(`${opts.socket}.api`),
  };
  switch (command) {
    case "serve":
      return serve(opts);
    case "env-file":
      process.stdout.write(
        renderEnvFile({
          node: opts.node ?? process.execPath,
          script: opts.script ?? realpathSync(process.argv[1]),
          socket: opts.socket,
          api,
          instance: opts.instance,
          namePrefix: opts["name-prefix"],
          publishIp: opts["publish-ip"],
          baseNetnsFile: baseNetnsPath(opts.socket),
        }),
      );
      return;
    case "relay":
      await (await runRelay(relayOptions)).stopped;
      process.exit(0);
      return;
    case "ensure": {
      const relayArgs = ["relay", ...rest];
      process.stdout.write(
        `${await ensureRelay({ ...relayOptions, script: process.argv[1], relayArgs })}\n`,
      );
      return;
    }
  }
}

main(process.argv.slice(2)).catch((error) => {
  console.error(`dind-bridge: ${error.message}`);
  process.exitCode = 1;
});
