package com.example.value

class Meter(val value: Double) extends AnyVal {
  def toFeet: Double = value * 3.28084
}
