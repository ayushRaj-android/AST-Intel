package com.example.traits

trait Drawable {
  def draw(): Unit
  def color: String = "black"
  def opacity: Double
}

trait Resizable {
  def resize(factor: Double): Unit
  def area: Double = 0.0
}
