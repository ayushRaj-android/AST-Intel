package com.example.annotations

@deprecated("use NewClass instead", "2.0")
class OldClass {
  @inline def fastMethod(): Int = 42

  @throws(classOf[Exception])
  def riskyMethod(): Unit = ()
}

@SerialVersionUID(1L)
class Versioned(val data: String)
