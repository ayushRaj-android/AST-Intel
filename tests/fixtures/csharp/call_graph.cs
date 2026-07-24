// Fixture for call-graph extraction tests.
// Contains various call patterns for testing edge extraction.

namespace CallGraph
{
    public static class Helpers
    {
        public static int Helper()
        {
            return 42;
        }

        public static bool Validate(string data)
        {
            return data.Length > 0;
        }

        public static int Process(string data)
        {
            // Intra-file call to Validate
            if (Validate(data))
            {
                return Helper();
            }
            return 0;
        }

        public static string Transform(int value)
        {
            return value.ToString();
        }

        public static string Orchestrate(string data)
        {
            var result = Process(data);
            return Transform(result);
        }
    }

    public class Calculator
    {
        private int _value = 0;

        public void Add(int x)
        {
            _value += x;
        }

        public string Compute(string data)
        {
            // Method calling static function (intra-file)
            var v = Helpers.Process(data);
            Add(v);
            // Method calling method on self
            Reset();
            return Helpers.Transform(_value);
        }

        public void Reset()
        {
            _value = 0;
        }
    }
}
