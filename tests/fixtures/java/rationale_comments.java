package com.example;

// NOTE: This is a design decision comment
public class RationaleExample {
    // HACK: Workaround for upstream bug #123
    public void workaround() {}

    // TODO: Implement proper caching
    public void fetchData() {}

    // WHY: We use a map here because lookup performance matters
    // PERF: O(1) amortized lookup time
    public void lookup() {}

    // FIXME: This should handle null inputs
    public String format(String input) {
        return input;
    }
}
