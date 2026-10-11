#!/usr/bin/env node
// Container-only entrypoint; compose supplies the DinD address.
import { startRelay } from "./relay.mjs";

async function main() {
  const publishHost = process.env.CLAUDE_DEVCONTAINER_DIND_ADDRESS;
  if (!publishHost) throw new Error("CLAUDE_DEVCONTAINER_DIND_ADDRESS is required");
  const relay = await startRelay({
    dockerSocket: "/var/run/dind/docker.sock",
    api: { host: "127.0.0.1", port: 2375 },
    publishHost,
    publishIp: "0.0.0.0",
    onWarning: (error) => {
      process.stderr.write(`${new Date().toISOString()} ${error.message}\n`);
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

main().catch((error) => {
  console.error(`dind-bridge: ${error.message}`);
  process.exitCode = 1;
});
