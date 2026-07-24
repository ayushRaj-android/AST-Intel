package com.example

package object utils {
  type Predicate[A] = A => Boolean
  val DefaultTimeout: Int = 30
  def identity[A](x: A): A = x
}
