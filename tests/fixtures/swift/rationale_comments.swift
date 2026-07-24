class Calculator {
    // WHY: Using Double for precision
    func divide(a: Double, b: Double) -> Double {
        // HACK: avoid division by zero
        if b == 0.0 { return 0.0 }
        return a / b
    }

    // TODO: add logging support
    func multiply(a: Double, b: Double) -> Double {
        return a * b
    }
}
