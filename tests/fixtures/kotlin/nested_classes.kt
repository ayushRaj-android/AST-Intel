package com.example.nested

class Outer(val x: Int) {
    inner class Inner(val y: Int) {
        fun sum(): Int = x + y
    }

    class Nested {
        fun greet(): String = "Hello from Nested"
    }

    enum class Status {
        ACTIVE,
        INACTIVE
    }
}
