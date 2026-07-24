package com.example.inheritance

open class Animal(val name: String) {
    open fun speak(): String = "..."
}

class Dog(name: String) : Animal(name) {
    override fun speak(): String = "Woof!"
}

interface Swimmable {
    fun swim(): String
}

interface Flyable {
    fun fly(): String
}

class Duck(name: String) : Animal(name), Swimmable, Flyable {
    override fun speak(): String = "Quack!"
    override fun swim(): String = "Swimming"
    override fun fly(): String = "Flying"
}
