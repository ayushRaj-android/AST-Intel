package com.example.rationale

class Calculator {
    // SAFETY: Division by zero is handled by returning NaN
    fun safeDivide(a: Double, b: Double): Double {
        if (b == 0.0) return Double.NaN
        return a / b
    }

    // TODO: Add support for complex numbers
    fun multiply(a: Double, b: Double): Double {
        return a * b
    }

    // HACK: Using string conversion for precision
    fun preciseAdd(a: Double, b: Double): Double {
        return (a.toBigDecimal() + b.toBigDecimal()).toDouble()
    }

    // FIXME: This doesn't handle negative exponents
    fun power(base: Double, exp: Int): Double {
        var result = 1.0
        repeat(exp) { result *= base }
        return result
    }
}
