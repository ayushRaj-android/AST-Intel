package com.example.contracts

interface Repository<T> {
    fun findById(id: Long): T?
    fun findAll(): List<T>
    fun save(entity: T): T
    fun delete(entity: T)
}

interface Cacheable {
    val cacheKey: String
    fun invalidateCache()
}

abstract class BaseService(private val name: String) {
    abstract fun start()
    abstract fun stop()

    fun status(): String {
        return "$name is running"
    }
}
