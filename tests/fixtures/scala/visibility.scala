package com.example.visibility

class VisibilityDemo {
  val publicVal: Int = 1
  private val privateVal: Int = 2
  protected val protectedVal: Int = 3
  private[visibility] val packageVal: Int = 4
  private[this] val instanceVal: Int = 5

  def publicMethod(): Unit = ()
  private def privateMethod(): Unit = ()
  protected def protectedMethod(): Unit = ()
  protected[visibility] def packageProtectedMethod(): Unit = ()
}
