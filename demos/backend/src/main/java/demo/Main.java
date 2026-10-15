package demo;

import io.javalin.Javalin;
import java.util.Map;

public class Main {
    public static void main(String[] args) {
        MessageRepository repo = new MessageRepository(
            env("JDBC_URL", "jdbc:postgresql://localhost:5432/appdb"),
            env("JDBC_USER", "app"),
            env("JDBC_PASSWORD", "app"));
        repo.init();
        Javalin.create(cfg -> {
            cfg.routes.get("/api/health", ctx -> ctx.json(Map.of("status", "ok")));
            cfg.routes.get("/api/message", ctx -> ctx.json(Map.of("message", repo.message())));
        }).start(8080);
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value != null ? value : fallback;
    }
}
