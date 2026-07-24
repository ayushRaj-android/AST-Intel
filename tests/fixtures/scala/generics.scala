package com.example.generics

class Container[+A](val value: A) {
  def map[B](f: A => B): Container[B] = new Container(f(value))
}

trait Comparable[A <: Ordered[A]] {
  def compare(other: A): Int
}

class Pair[A, B](val first: A, val second: B)
