<?php

/**
 * A helper function
 */
function helper(string $input, int $count = 10): array {
    return array_fill(0, $count, $input);
}

function greet(string $name): string {
    return "Hello, " . $name;
}

function process(): void {
    // do nothing
}
