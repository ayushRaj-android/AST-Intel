package com.example.types

type StringList = List[String]
type Predicate[A] = A => Boolean
type Callback = () => Unit

trait HasType {
  type Element
  type Container[A]
}
