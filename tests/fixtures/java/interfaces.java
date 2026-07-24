package com.example.services;

import java.util.List;

public interface IService {
    void handle(String request);
    String getName();
}

public interface GenericMapper<T, R> {
    R apply(T input);
    default R applyOrDefault(T input, R fallback) {
        R result = apply(input);
        return result != null ? result : fallback;
    }
}

@FunctionalInterface
public interface Predicate<T> {
    boolean test(T value);
}

public interface IStorageHelper extends IService {
    byte[] retrieve(String key);
    void store(String key, byte[] data);
}
