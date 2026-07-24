package com.example.curried

def fold[A, B](list: List[A])(init: B)(f: (B, A) => B): B =
  list.foldLeft(init)(f)

def configure(host: String)(port: Int)(debug: Boolean): String =
  s"$host:$port (debug=$debug)"
