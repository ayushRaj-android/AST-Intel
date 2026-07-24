package com.example.demo;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class UserController {

    @GetMapping("/users")
    public String listUsers() {
        return "[]";
    }

    @PostMapping("/users")
    public String createUser() {
        return "created";
    }

    @PutMapping("/users/{id}")
    public String updateUser(@PathVariable String id) {
        return "updated";
    }

    @DeleteMapping("/users/{id}")
    public String deleteUser(@PathVariable String id) {
        return "deleted";
    }

    private void internalHelper() {
        // No mapping here
    }
}
