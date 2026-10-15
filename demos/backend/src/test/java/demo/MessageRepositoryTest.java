package demo;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;
import org.testcontainers.containers.PostgreSQLContainer;

// postgres:16-alpine is one of the images claude.toml loads into DinD; Ryuk is
// pinned to the loaded version in testcontainers.properties.
class MessageRepositoryTest {
    @Test
    void readsTheSeededMessage() {
        try (PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")) {
            postgres.start();
            MessageRepository repo = new MessageRepository(
                postgres.getJdbcUrl(), postgres.getUsername(), postgres.getPassword());
            repo.init();
            assertEquals("hello from postgres", repo.message());
        }
    }
}
