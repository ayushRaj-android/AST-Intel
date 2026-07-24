package com.example.models;

import java.util.List;
import java.util.Map;
import java.io.Serializable;

/// Simple model class with fields and methods.
public class Job implements Serializable {
    private String name;
    public int priority;
    protected List<String> tags;

    public Job(String name, int priority) {
        this.name = name;
        this.priority = priority;
    }

    public void reset() {
        name = "";
    }

    private static int computeHash(String input) {
        return input.hashCode();
    }
}

class InternalHelper {
    public String format(String value) {
        return "[" + value + "]";
    }
}

public class GenericContainer<T extends Comparable<T>> {
    private T value;
    public int count;

    public void add(T item) {
        count++;
    }

    public <U> U transform(T input) {
        return null;
    }
}
