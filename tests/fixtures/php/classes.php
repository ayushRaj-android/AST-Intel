<?php

/**
 * A basic user class
 * @template T
 */
#[Entity]
#[Table("users")]
final class User {
    public string $name;
    protected ?int $age = null;
    private array $tags = [];
    public readonly string $id;

    /**
     * Create a new user
     */
    public function __construct(string $name, int $age = 0) {
        $this->name = $name;
        $this->age = $age;
    }

    public static function create(string $name): self {
        return new self($name);
    }

    protected function validate(): bool {
        return strlen($this->name) > 0;
    }

    public function getName(): string {
        return $this->name;
    }
}

readonly class Config {
    public function __construct(
        public string $dsn,
        public int $timeout = 30,
    ) {}
}
