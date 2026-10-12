package demo;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;

public class MessageRepository {
    private final String url;
    private final String user;
    private final String password;

    public MessageRepository(String url, String user, String password) {
        this.url = url;
        this.user = user;
        this.password = password;
    }

    public void init() {
        try (Connection c = DriverManager.getConnection(url, user, password);
                Statement s = c.createStatement()) {
            s.execute("CREATE TABLE IF NOT EXISTS message (id int PRIMARY KEY, body text NOT NULL)");
            s.execute("INSERT INTO message VALUES (1, 'hello from postgres') ON CONFLICT (id) DO NOTHING");
        } catch (SQLException e) {
            throw new IllegalStateException("could not initialize the database", e);
        }
    }

    public String message() {
        try (Connection c = DriverManager.getConnection(url, user, password);
                Statement s = c.createStatement();
                var rs = s.executeQuery("SELECT body FROM message WHERE id = 1")) {
            return rs.next() ? rs.getString(1) : "";
        } catch (SQLException e) {
            throw new IllegalStateException("could not read the message", e);
        }
    }
}
