package com.example;

import java.util.List;
import java.util.Map;

public class CallGraphExample {
    public void outer() {
        helper();
        process("test");
    }

    private void helper() {
        List.of("a");
    }

    public String process(String input) {
        return transform(input);
    }

    private String transform(String s) {
        return s.toUpperCase();
    }
}
