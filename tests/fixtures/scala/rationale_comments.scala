package com.example.rationale

class Calculator {
  // WHY: Using Double for precision
  def divide(a: Double, b: Double): Double = {
    // HACK: avoid division by zero
    if (b == 0.0) return 0.0
    a / b
  }

  // TODO: add logging support
  def multiply(a: Double, b: Double): Double = a * b
}
