# Fixture for call-graph extraction tests.
# Contains various call patterns for testing edge extraction.


def helper():
    return 42


def validate(data):
    return len(data) > 0


def process(data):
    # Intra-file call to validate
    if validate(data):
        return helper()
    return 0


def transform(value):
    return str(value)


def orchestrate(data):
    result = process(data)
    return transform(result)


class Calculator:
    def __init__(self):
        self.value = 0

    def add(self, x):
        self.value += x

    def compute(self, data):
        # Method calling free function (intra-file)
        v = process(data)
        self.add(v)
        # Method calling method on self
        self.reset()
        return transform(self.value)

    def reset(self):
        self.value = 0


def use_calculator():
    calc = Calculator()
    calc.add(10)
    result = calc.compute("test")
    print(result)
