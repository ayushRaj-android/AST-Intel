package com.example.generics

class Box<T>(val value: T) {
    fun <R> map(transform: (T) -> R): Box<R> {
        return Box(transform(value))
    }
}

fun <T : Comparable<T>> maxOf(a: T, b: T): T {
    return if (a > b) a else b
}

interface Transformer<in I, out O> {
    fun transform(input: I): O
}
