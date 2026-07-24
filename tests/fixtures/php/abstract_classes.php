<?php

abstract class Repository {
    abstract public function find(int $id): ?object;
    abstract protected function query(string $sql): array;

    public function all(): array {
        return [];
    }

    public function count(): int {
        return 0;
    }
}
