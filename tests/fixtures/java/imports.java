package com.example.imports;

import java.util.List;
import java.util.Map;
import java.util.HashMap;
import java.util.stream.Collectors;
import static java.lang.Math.abs;
import static java.util.Collections.emptyList;

public class ImportExample {
    public void process() {
        List.of("a", "b");
        Map.of("key", "value");
        HashMap<String, String> map = new HashMap<>();
        int v = abs(-1);
    }
}
