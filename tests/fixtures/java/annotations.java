package com.example.annotations;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface HttpGet {
    String value() default "";
}

@Deprecated
public class LegacyService {
    @Override
    public String toString() {
        return "legacy";
    }

    @SuppressWarnings("unchecked")
    public void process() {}
}

public class Controller {
    @HttpGet("/api/items")
    public String getItems() {
        return "[]";
    }

    @HttpGet("/api/users")
    @Deprecated
    public String getUsers() {
        return "[]";
    }
}
