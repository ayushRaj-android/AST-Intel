package com.example.async

import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers

suspend fun fetchData(url: String): String {
    delay(1000)
    return "data from $url"
}

class ApiClient {
    suspend fun get(endpoint: String): String {
        return withContext(Dispatchers.IO) {
            fetchData(endpoint)
        }
    }

    suspend fun post(endpoint: String, body: String): String {
        return withContext(Dispatchers.IO) {
            "posted to $endpoint"
        }
    }
}
