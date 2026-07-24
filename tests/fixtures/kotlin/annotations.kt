package com.example.annotations

@Target(AnnotationTarget.CLASS)
@Retention(AnnotationRetention.RUNTIME)
annotation class Service(val name: String = "")

@Target(AnnotationTarget.FUNCTION)
annotation class Transactional

@Service("userService")
class UserService {
    @Transactional
    fun createUser(name: String): Boolean {
        return true
    }

    @Deprecated("Use createUser instead", ReplaceWith("createUser(name)"))
    fun addUser(name: String): Boolean {
        return createUser(name)
    }
}
