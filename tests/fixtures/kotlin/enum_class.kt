package com.example.enums

enum class Color(val hex: String) {
    RED("#FF0000"),
    GREEN("#00FF00"),
    BLUE("#0000FF");

    fun toRgb(): List<Int> {
        val r = hex.substring(1, 3).toInt(16)
        val g = hex.substring(3, 5).toInt(16)
        val b = hex.substring(5, 7).toInt(16)
        return listOf(r, g, b)
    }
}

enum class Direction {
    NORTH,
    SOUTH,
    EAST,
    WEST
}
