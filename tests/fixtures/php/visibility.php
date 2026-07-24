<?php

class AccessControl {
    public string $publicField;
    protected string $protectedField;
    private string $privateField;
    public readonly string $readonlyField;

    public function publicMethod(): void {}
    protected function protectedMethod(): void {}
    private function privateMethod(): void {}
}
