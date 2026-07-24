package com.example.singletons

object DatabaseConfig {
    val host: String = "localhost"
    val port: Int = 5432

    fun connectionString(): String {
        return "jdbc:postgresql://$host:$port"
    }
}

class Logger private constructor(val tag: String) {
    companion object {
        const val DEFAULT_TAG = "APP"
        private var instance: Logger? = null

        fun getInstance(tag: String = DEFAULT_TAG): Logger {
            return instance ?: Logger(tag).also { instance = it }
        }
    }

    fun info(message: String) {
        println("[$tag] INFO: $message")
    }
}
