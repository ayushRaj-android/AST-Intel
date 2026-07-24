package com.example.implicits

trait Ordering[T] {
  def compare(a: T, b: T): Int
}

def sorted[T](list: List[T])(implicit ord: Ordering[T]): List[T] = list

given intOrdering: Ordering[Int] = new Ordering[Int] {
  def compare(a: Int, b: Int): Int = a - b
}

def process(data: List[Int])(using ord: Ordering[Int]): List[Int] = data
