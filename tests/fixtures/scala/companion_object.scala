package com.example.companion

class Person(val name: String, val age: Int) {
  def greet(): String = s"Hello, I'm $name"
}

object Person {
  def apply(name: String): Person = new Person(name, 0)
  def fromMap(data: Map[String, Any]): Person = {
    new Person(data("name").toString, data("age").asInstanceOf[Int])
  }
}
