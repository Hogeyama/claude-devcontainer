import { describe, expect, it } from 'vitest';
import { GenericContainer, Wait } from 'testcontainers';

// Runs against DinD inside the agent, and against the local Docker on the
// host. busybox:1.37 is one of the images claude.toml loads into DinD.
describe('testcontainers through DinD', () => {
  it(
    'serves a page from a busybox container',
    async () => {
      const container = await new GenericContainer('busybox:1.37')
        .withCommand([
          'sh',
          '-c',
          'mkdir /www; echo dind-ok > /www/index.html; exec httpd -f -p 8080 -h /www',
        ])
        .withExposedPorts(8080)
        .withWaitStrategy(Wait.forHttp('/', 8080).forStatusCode(200))
        .withStartupTimeout(30_000)
        .start();
      try {
        const url = `http://${container.getHost()}:${container.getMappedPort(8080)}/`;
        const response = await fetch(url);
        expect(await response.text()).toBe('dind-ok\n');
      } finally {
        await container.stop();
      }
    },
    60_000,
  );
});
