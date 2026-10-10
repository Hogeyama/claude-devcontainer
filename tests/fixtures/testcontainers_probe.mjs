import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync, readlinkSync } from 'node:fs';
const require = createRequire(process.env.SDK_PACKAGE_JSON);
const {GenericContainer, Wait} = require('testcontainers');
assert.notEqual(readlinkSync('/proc/self/ns/net'), readFileSync('/run/dind-bridge/base-netns', 'utf8').trim());
const fixture = () => new GenericContainer('busybox:1.37')
 .withCommand(['sh', '-c', 'mkdir -p /www; echo sdk-ok > /www/index.html; exec httpd -f -p 8080 -h /www'])
 .withExposedPorts(8080).withWaitStrategy(Wait.forHttp('/', 8080).forStatusCode(200)).withStartupTimeout(30000);
const container = await fixture().start();
try {
 const response = await fetch(`http://${container.getHost()}:${container.getMappedPort(8080)}/`);
 assert.equal(await response.text(), 'sdk-ok\n');
 const result = await container.exec(['sh', '-c', 'printf exec-ok']);
 assert.equal(result.exitCode, 0); assert.equal(result.output, 'exec-ok');
} finally { await container.stop(); }
await fixture().withName(process.env.SDK_ORPHAN_NAME).start();
console.log('actual-testcontainers-start-wait-port-exec-ok');
process.exit(0); // Ryuk must remove the intentionally orphaned fixture.
