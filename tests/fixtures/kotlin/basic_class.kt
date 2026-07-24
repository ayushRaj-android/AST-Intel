package com.example.models

import java.util.UUID

/**
 * A basic user class with primary constructor.
 */
class User(
    val id: UUID,
    val name: String,
    var email: String
) {
    private var loginCount: Int = 0

    fun displayName(): String {
        return "$name <$email>"
    }

    fun incrementLogin() {
        loginCount++
    }
}
