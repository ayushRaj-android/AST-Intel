<?php

class Animal {
    public string $name;
}

interface Swimmable {
    public function swim(): void;
}

interface Flyable {
    public function fly(): void;
}

trait HasLegs {
    public function walk(): void {}
}

class Duck extends Animal implements Swimmable, Flyable {
    use HasLegs;

    public function swim(): void {}
    public function fly(): void {}
    public function quack(): void {}
}
