# Module used as a namespace
module Validators
  class EmailValidator
    def validate(email)
      email.include?("@")
    end
  end
end

# Module used as a mixin (trait)
module Serializable
  def to_hash
    instance_variables.each_with_object({}) do |var, hash|
      hash[var.to_s.delete("@")] = instance_variable_get(var)
    end
  end

  def to_json
    to_hash.to_json
  end
end

# Module with both namespace and mixin behavior
module Logging
  LOG_LEVEL = "INFO"

  def log(message)
    puts "[#{LOG_LEVEL}] #{message}"
  end

  class Logger
    def initialize(output)
      @output = output
    end
  end
end

# Class including modules
class Document
  include Serializable
  include Comparable

  attr_reader :title, :body

  def initialize(title, body)
    @title = title
    @body = body
  end

  def <=>(other)
    title <=> other.title
  end
end
