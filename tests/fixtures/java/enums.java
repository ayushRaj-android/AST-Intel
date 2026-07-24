package com.example.models;

public enum Status {
    ACTIVE,
    INACTIVE,
    PENDING;

    public String label() {
        return name().toLowerCase();
    }
}

public enum HttpMethod {
    GET("GET"),
    POST("POST"),
    PUT("PUT"),
    DELETE("DELETE");

    private final String value;

    HttpMethod(String value) {
        this.value = value;
    }

    public String getValue() {
        return value;
    }
}

public enum Color {
    RED,
    GREEN,
    BLUE
}
