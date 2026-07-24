<?php

/**
 * A cacheable interface
 */
interface Cacheable {
    public function cacheKey(): string;
    public function cacheTTL(): int;
    const DEFAULT_TTL = 3600;
}

interface Renderable extends Cacheable {
    public function render(): string;
}
