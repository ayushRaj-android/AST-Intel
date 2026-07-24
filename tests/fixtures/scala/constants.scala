package com.example.constants

object Constants {
  final val MAX_SIZE: Int = 100
  final val APP_NAME: String = "MyApp"
  final val VERSION: Double = 1.0
}

class Settings {
  val timeout: Int = 30
  lazy val config: Map[String, String] = Map("a" -> "b")
}
