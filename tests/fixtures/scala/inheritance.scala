package com.example.inheritance

trait Serializable
trait Logging {
  def log(msg: String): Unit
}
trait Drawable {
  def draw(): Unit
}

class Service(val name: String) extends Serializable with Logging with Drawable {
  override def log(msg: String): Unit = println(msg)
  override def draw(): Unit = ()
}

abstract class Base(val id: Int)
class Child(id: Int, val extra: String) extends Base(id) with Logging {
  override def log(msg: String): Unit = println(s"[$id] $msg")
}
