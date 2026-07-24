using System;
using System.Threading.Tasks;
using System.Collections.Generic;

namespace TestProject.Models
{
    /// <summary>Positional record type.</summary>
    public record Point(int X, int Y);

    /// <summary>Record with body.</summary>
    public record Person(string Name, int Age)
    {
        public string Greeting => $"Hello, {Name}!";
    }

    /// <summary>Value type struct.</summary>
    public struct Coordinate
    {
        public double Latitude { get; set; }
        public double Longitude { get; set; }

        public double DistanceTo(Coordinate other) => 0.0;
    }

    /// <summary>Abstract base class (trait-like).</summary>
    public abstract class HandlerBase
    {
        public abstract Task HandleAsync(string input);
        public virtual void OnError(Exception ex) { }
        protected abstract string GetName();
    }

    /// <summary>Concrete handler.</summary>
    public class ConcreteHandler : HandlerBase
    {
        public override async Task HandleAsync(string input)
        {
            await Task.CompletedTask;
        }

        public override void OnError(Exception ex)
        {
            Console.WriteLine(ex.Message);
        }

        protected override string GetName() => "ConcreteHandler";
    }

    /// <summary>Static utility class.</summary>
    public static class MathUtils
    {
        public const double Pi = 3.14159;
        public const string Version = "1.0.0";
        public static readonly int MaxRetries = 3;

        public static int Add(int a, int b) => a + b;
        public static async Task<int> AddAsync(int a, int b) => await Task.FromResult(a + b);
        private static double Square(double x) => x * x;
    }

    /// <summary>Custom exception.</summary>
    public class AppException : Exception
    {
        public int ErrorCode { get; }

        public AppException(string message, int errorCode) : base(message)
        {
            ErrorCode = errorCode;
        }
    }
}
