#include "config.h"
#include <stdio.h>
#include <stdlib.h>

// NOTE: Main processing entry-point.
void process(int* data, size_t len) {
    for (size_t i = 0; i < len; i++) {
        data[i] *= 2;
    }
}

static int helper(int x, int y) {
    return x + y;
}

int compute(int a, int b) {
    int sum = helper(a, b);
    process(&sum, 1);
    return sum;
}
