module Printable
  def to_s
    inspect
  end
end

module Loggable
  def log(msg)
    puts msg
  end
end

class Base
  include Printable

  def initialize
    @created_at = Time.now
  end
end

class Child < Base
  include Loggable

  def initialize(name)
    super()
    @name = name
  end
end
