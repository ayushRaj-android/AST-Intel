package com.example.constants

const val MAX_RETRIES = 3
const val API_BASE_URL = "https://api.example.com"

val TIMEOUT_MS: Long = 5000L

typealias StringMap = Map<String, String>
typealias Predicate<T> = (T) -> Boolean
typealias UserList = List<User>
