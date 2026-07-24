package com.example.imports

import java.util.UUID
import scala.collection.mutable.{ListBuffer, ArrayBuffer}
import scala.concurrent._
import java.io.{InputStream => IS}

class Importer {
  val id: UUID = UUID.randomUUID()
  val buffer: ListBuffer[String] = ListBuffer.empty
}
