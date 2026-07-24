# Nested classes
class Outer
  class Inner
    def inner_method
      "inner"
    end
  end

  def outer_method
    "outer"
  end
end

# Struct.new shorthand
Point = Struct.new(:x, :y)

# Class with question mark and bang methods
class Validator
  def valid?
    true
  end

  def validate!
    raise "Invalid" unless valid?
  end
end

# Dynamic methods
class DynamicProxy
  def method_missing(name, *args)
    # dynamic dispatch
  end

  def respond_to_missing?(name, include_private = false)
    true
  end
end
