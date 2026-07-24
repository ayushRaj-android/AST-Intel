<?php

enum Status {
    case Active;
    case Inactive;
    case Pending;
}

enum Color: string {
    case Red = 'red';
    case Blue = 'blue';
    case Green = 'green';

    public function label(): string {
        return ucfirst($this->value);
    }
}

enum Priority: int implements Cacheable {
    case Low = 1;
    case Medium = 5;
    case High = 10;

    public function cacheKey(): string {
        return 'priority_' . $this->value;
    }

    public function cacheTTL(): int {
        return 3600;
    }
}
