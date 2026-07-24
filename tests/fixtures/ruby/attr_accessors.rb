class Person
  attr_accessor :name, :email
  attr_reader :id
  attr_writer :password

  def initialize(id, name, email)
    @id = id
    @name = name
    @email = email
  end
end
