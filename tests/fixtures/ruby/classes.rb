# A simple class with no parent
class SimpleClass
end

# Class with initialize and fields
class User
  # Create a new user
  def initialize(name, age)
    @name = name
    @age = age
    @active = true
  end

  def greet
    "Hello, #{@name}"
  end
end

# Class with inheritance
class Admin < User
  def initialize(name, age, role)
    super(name, age)
    @role = role
  end

  def admin?
    true
  end
end

# Class with attr_accessor fields
class Container
  attr_accessor :items

  def initialize
    @items = []
  end

  def add(item)
    @items << item
  end
end
