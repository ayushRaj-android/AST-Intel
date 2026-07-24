package com.example;

import java.util.List;
import java.util.Map;
import java.util.ArrayList;
import java.io.Serializable;
import static java.lang.Math.abs;

/// Main application entry point.
public class Application implements Serializable {
    public static final String VERSION = "1.0.0";
    private final String name;

    public Application(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }

    public void run(String[] args) {
        List<String> items = new ArrayList<>();
        items.add("hello");
        Map.of("key", "value");
        int v = abs(-42);
    }
}

public interface Plugin {
    void init();
    default void shutdown() {}
}

public abstract class BasePlugin implements Plugin {
    protected String id;
    public abstract String getVersion();
}

public class CorePlugin extends BasePlugin {
    @Override
    public void init() {}

    @Override
    public String getVersion() {
        return "1.0";
    }
}

public enum AppState {
    STARTING,
    RUNNING,
    STOPPED;
}

public record Settings(String host, int port, boolean debug) {}
