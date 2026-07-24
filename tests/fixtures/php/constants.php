<?php

const APP_VERSION = '1.0.0';
const MAX_RETRIES = 3;

class Settings {
    const MODE_DEBUG = 'debug';
    public const MODE_PROD = 'production';
    protected const SECRET_KEY = 'abc123';

    private static array $cache = [];
    public static int $instanceCount = 0;
}
