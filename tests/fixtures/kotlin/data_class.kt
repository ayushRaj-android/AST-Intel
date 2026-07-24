package com.example.dto

data class UserDTO(
    val id: Long,
    val username: String,
    val email: String,
    val isActive: Boolean = true
)

data class Coordinate(val x: Double, val y: Double) {
    fun distanceTo(other: Coordinate): Double {
        val dx = x - other.x
        val dy = y - other.y
        return Math.sqrt(dx * dx + dy * dy)
    }
}
