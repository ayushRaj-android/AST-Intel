<?php

/**
 * User class PHPDoc
 */
class Documented {
    /**
     * Get user name
     * @return string The user name
     */
    public function getName(): string {
        return '';
    }
}

/**
 * A top-level documented function
 * @param string $input The input
 * @return string The output
 */
function documented(string $input): string {
    return $input;
}
