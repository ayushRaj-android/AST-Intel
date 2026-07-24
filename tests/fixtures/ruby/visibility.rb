class AccessControl
  def public_method
    "public"
  end

  private

  def private_method
    "private"
  end

  def another_private
    "also private"
  end

  protected

  def protected_method
    "protected"
  end

  public

  def back_to_public
    "public again"
  end
end

class TargetedVisibility
  def method_a; end
  def method_b; end
  def method_c; end

  private :method_b
  protected :method_c
end
