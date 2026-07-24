<?php

function caller(): void {
    callee();
    helper("test");
}

function callee(): void {}

function helper(string $input): string {
    return strtoupper($input);
}
