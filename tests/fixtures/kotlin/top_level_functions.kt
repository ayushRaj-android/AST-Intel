package com.example.topfunctions

import java.time.LocalDateTime

fun greet(name: String): String {
    return "Hello, $name!"
}

fun add(a: Int, b: Int): Int = a + b

inline fun <reified T> typeNameOf(): String {
    return T::class.simpleName ?: "Unknown"
}

infix fun Int.times(str: String): String {
    return str.repeat(this)
}

operator fun Pair<Int, Int>.plus(other: Pair<Int, Int>): Pair<Int, Int> {
    return Pair(first + other.first, second + other.second)
}
