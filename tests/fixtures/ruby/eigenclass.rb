class Configuration
  class << self
    def instance
      @instance ||= new
    end

    def reset!
      @instance = nil
    end

    private

    def load_defaults
      {}
    end
  end

  def initialize
    @settings = {}
  end

  def get(key)
    @settings[key]
  end
end
