package com.example.demo;

import org.springframework.web.client.RestTemplate;

public class UserClient {

    private final RestTemplate restTemplate;

    public UserClient(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public String getUsers() {
        return restTemplate.getForObject("http://user-service/api/users", String.class);
    }

    public String createUser(String body) {
        return restTemplate.postForObject("http://user-service/api/users", body, String.class);
    }

    public void deleteUser(String id) {
        restTemplate.delete("http://user-service/api/users/" + id);
    }

    private void notHttp() {
        String result = "hello".substring(0, 3);
    }
}
