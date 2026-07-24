<?php

/**
 * A generic container
 * @template T
 * @template U of Comparable
 */
class Container {
    public function add($item): void {}
    public function get(): mixed { return null; }
}
