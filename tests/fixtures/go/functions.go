package functions

import (
	"context"
	"fmt"
)

// ProcessData is an exported free function.
func ProcessData(ctx context.Context, data []byte) ([]byte, error) {
	_ = ctx
	return data, nil
}

// helperFunc is an unexported free function.
func helperFunc(input string) string {
	return fmt.Sprintf("processed: %s", input)
}

// MultiReturn returns multiple values.
func MultiReturn(a, b int) (int, int, error) {
	return a + b, a * b, nil
}

// NoReturn has no return type.
func NoReturn(msg string) {
	fmt.Println(msg)
}

// GenericFunc is a generic function.
func GenericFunc[T comparable](items []T, target T) int {
	for i, item := range items {
		if item == target {
			return i
		}
	}
	return -1
}

// VariadicFunc accepts variadic arguments.
func VariadicFunc(prefix string, values ...int) []string {
	result := make([]string, len(values))
	for i, v := range values {
		result[i] = fmt.Sprintf("%s_%d", prefix, v)
	}
	return result
}

// HigherOrderFunc takes a function parameter.
func HigherOrderFunc(fn func(int) bool, items []int) []int {
	var result []int
	for _, item := range items {
		if fn(item) {
			result = append(result, item)
		}
	}
	return result
}
