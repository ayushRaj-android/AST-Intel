// Fixture for call-graph extraction tests.
// Contains various call patterns for testing edge extraction.

fn helper() -> i32 {
    42
}

fn validate(data: &str) -> bool {
    !data.is_empty()
}

fn process(data: &str) -> i32 {
    // Intra-file call to validate
    if validate(data) {
        helper()
    } else {
        0
    }
}

fn transform(value: i32) -> String {
    // Calls to external (unresolved) functions
    format!("{}", value)
}

fn orchestrate(data: &str) -> String {
    let result = process(data);
    transform(result)
}

struct Calculator {
    value: i32,
}

impl Calculator {
    fn new() -> Self {
        Calculator { value: 0 }
    }

    fn add(&mut self, x: i32) {
        self.value += x;
    }

    fn compute(&mut self, data: &str) -> String {
        // Method calling free function (intra-file)
        let v = process(data);
        self.add(v);
        // Method calling method on self
        self.reset();
        transform(self.value)
    }

    fn reset(&mut self) {
        self.value = 0;
    }
}

fn use_calculator() {
    let mut calc = Calculator::new();
    calc.add(10);
    let _result = calc.compute("test");
}
