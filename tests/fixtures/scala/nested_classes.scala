package com.example.nested

class Outer(val name: String) {
  class Inner(val value: Int) {
    def describe(): String = s"$name: $value"
  }

  object InnerCompanion {
    def create(v: Int): Inner = new Inner(v)
  }
}

object Container {
  class Item(val id: Int)
  object ItemFactory {
    def make(id: Int): Item = new Item(id)
  }
}
