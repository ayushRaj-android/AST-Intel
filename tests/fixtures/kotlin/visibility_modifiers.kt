package com.example.access

public class PublicApi {
    public fun publicMethod(): String = "public"
    internal fun internalMethod(): String = "internal"
    protected open fun protectedMethod(): String = "protected"
    private fun privateMethod(): String = "private"

    public val publicField: String = "public"
    internal val internalField: String = "internal"
    private val privateField: String = "private"
}

private class InternalHelper {
    fun doWork(): Unit {}
}
