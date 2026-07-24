name := "my-scala-app"
version := "1.0.0"
scalaVersion := "3.3.1"

libraryDependencies ++= Seq(
  "org.typelevel" %% "cats-core" % "2.10.0",
  "com.typesafe.akka" %% "akka-actor" % "2.8.5",
  "org.scalatest" %% "scalatest" % "3.2.17" % Test,
  "ch.qos.logback" % "logback-classic" % "1.4.11"
)

libraryDependencies += "io.circe" %% "circe-core" % "0.14.6"
