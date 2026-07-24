package com.example.abstracts

/**
 * An abstract animal class.
 */
abstract class Animal(val name: String) {
  def speak(): String
  def describe(): String = s"Animal: $name"
}

abstract class Vehicle {
  val wheels: Int
  def drive(): Unit
  def honk(): String = "Beep!"
}
