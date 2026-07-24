package com.example.models

import java.util.UUID

/**
 * A basic user class with primary constructor.
 */
class User(val id: UUID, var name: String, email: String) {
  private val createdAt: Long = System.currentTimeMillis()

  def greet(): String = s"Hello, $name"

  private def validate(): Boolean = name.nonEmpty

  override def toString: String = s"User($id, $name)"
}
