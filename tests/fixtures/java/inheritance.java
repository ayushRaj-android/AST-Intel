package com.example.services;

import java.util.List;

public abstract class BaseHandler {
    public abstract void handle();
    public abstract String process(String input);

    public void log(String msg) {
        System.out.println(msg);
    }

    protected int computeRetries() {
        return 3;
    }
}

public class DiskStorage implements IStorageHelper {
    private String basePath;

    @Override
    public void handle(String request) {
        // handle request
    }

    @Override
    public String getName() {
        return "DiskStorage";
    }

    @Override
    public byte[] retrieve(String key) {
        return new byte[0];
    }

    @Override
    public void store(String key, byte[] data) {
        // store data
    }
}

public class Worker extends BaseHandler implements Runnable {
    @Override
    public void handle() {}

    @Override
    public String process(String input) {
        return input.toUpperCase();
    }

    @Override
    public void run() {}
}
