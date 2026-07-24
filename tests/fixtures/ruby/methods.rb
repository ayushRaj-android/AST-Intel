# Module-level function
def greet(name)
  "Hello, #{name}"
end

# Function with default parameter
def connect(host, port = 8080)
  # establish connection
end

# Function with splat
def sum(*numbers)
  numbers.reduce(0, :+)
end

# Function with keyword arguments
def create_user(name:, email:, role: "user")
  { name: name, email: email, role: role }
end

# Function with double splat
def configure(**options)
  options
end

# Function with block parameter
def with_retry(attempts = 3, &block)
  block.call
end

class Calculator
  # Instance method
  def add(a, b)
    a + b
  end

  # Class method via self.
  def self.description
    "A simple calculator"
  end

  private

  def validate_input(value)
    raise ArgumentError unless value.is_a?(Numeric)
  end

  protected

  def internal_state
    @state
  end
end
