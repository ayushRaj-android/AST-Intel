package com.example.models

/**
 * A 2D point as a case class.
 */
case class Point(x: Double, y: Double) {
  def distance(other: Point): Double =
    math.sqrt(math.pow(x - other.x, 2) + math.pow(y - other.y, 2))
}

case class Config(host: String, port: Int, debug: Boolean = false)
