package com.example.extensions

extension (s: String)
  def greetWith(greeting: String): String = s"$greeting, $s!"
  def shout: String = s.toUpperCase

extension (n: Int)
  def isEven: Boolean = n % 2 == 0
