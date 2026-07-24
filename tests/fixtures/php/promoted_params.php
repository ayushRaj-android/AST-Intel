<?php

class UserDTO {
    public function __construct(
        public readonly string $name,
        protected int $age,
        private string $email = '',
    ) {}

    public function getName(): string {
        return $this->name;
    }
}
