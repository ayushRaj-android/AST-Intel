<?php

function acceptUnion(string|int $value): string|int {
    return $value;
}

function acceptNullable(?string $name): ?string {
    return $name;
}

function returnVoid(): void {}

function returnMixed(): mixed {
    return null;
}

class TypedProps {
    public string|int $flexible;
    public ?array $items = null;
}
