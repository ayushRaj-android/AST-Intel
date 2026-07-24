<?php

/**
 * Timestamps trait for created/updated dates
 */
trait Timestamps {
    public \DateTimeInterface $createdAt;

    abstract public function touch(): void;

    public function getCreatedAt(): \DateTimeInterface {
        return $this->createdAt;
    }

    protected function formatDate(\DateTimeInterface $date): string {
        return $date->format('Y-m-d');
    }
}

trait Loggable {
    public function log(string $message): void {
        echo $message;
    }
}
