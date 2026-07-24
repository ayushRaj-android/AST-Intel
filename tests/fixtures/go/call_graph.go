// Fixture for call-graph extraction tests.
// Contains various call patterns for testing edge extraction.

package callgraph

func helper() int {
	return 42
}

func validate(data string) bool {
	return len(data) > 0
}

func process(data string) int {
	// Intra-file call to validate
	if validate(data) {
		return helper()
	}
	return 0
}

func transform(value int) string {
	return fmt.Sprintf("%d", value)
}

func orchestrate(data string) string {
	result := process(data)
	return transform(result)
}

type Calculator struct {
	value int
}

func (c *Calculator) Add(x int) {
	c.value += x
}

func (c *Calculator) Compute(data string) string {
	// Method calling free function (intra-file)
	v := process(data)
	c.Add(v)
	// Method calling method on self
	c.Reset()
	return transform(c.value)
}

func (c *Calculator) Reset() {
	c.value = 0
}

func useCalculator() {
	calc := &Calculator{}
	calc.Add(10)
	result := calc.Compute("test")
	fmt.Println(result)
}
