// Fixture for call-graph extraction tests.
// Contains various call patterns for testing edge extraction.

function helper(): number {
    return 42;
}

function validate(data: string): boolean {
    return data.length > 0;
}

function process(data: string): number {
    // Intra-file call to validate
    if (validate(data)) {
        return helper();
    }
    return 0;
}

function transform(value: number): string {
    return String(value);
}

function orchestrate(data: string): string {
    const result = process(data);
    return transform(result);
}

class Calculator {
    private value: number = 0;

    add(x: number): void {
        this.value += x;
    }

    compute(data: string): string {
        // Method calling free function (intra-file)
        const v = process(data);
        this.add(v);
        // Method calling method on self
        this.reset();
        return transform(this.value);
    }

    reset(): void {
        this.value = 0;
    }
}

function useCalculator(): void {
    const calc = new Calculator();
    calc.add(10);
    const result = calc.compute("test");
    console.log(result);
}
