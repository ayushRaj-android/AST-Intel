<?php

class Worker {
    // NOTE: this is important for thread safety
    public function process(): void {}

    // HACK: workaround for legacy API
    public function legacyCall(): void {}

    // TODO: refactor this method
    public function transform(): void {}
}
