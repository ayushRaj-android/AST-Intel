package main

import (
	"net/http"
)

func fetchUsers() (*http.Response, error) {
	return http.Get("http://example.com/api/users")
}

func createUser(body string) (*http.Response, error) {
	return http.Post("http://example.com/api/users", "application/json", nil)
}

func customRequest() (*http.Response, error) {
	req, _ := http.NewRequest("DELETE", "http://example.com/api/users/1", nil)
	client := &http.Client{}
	return client.Do(req)
}

func notHttp() int {
	return len("hello")
}
