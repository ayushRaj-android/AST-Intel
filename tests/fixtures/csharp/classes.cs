using System;
using System.Collections.Generic;
using System.Runtime.Serialization;

namespace TestProject.Models
{
    /// <summary>Simple model class with properties.</summary>
    [DataContract]
    public class Job
    {
        [Required]
        [DataMember(Name = "id")]
        public Guid Id { get; set; }

        [Required]
        public string Name { get; set; }

        private readonly int _priority;
        public int Priority => _priority;

        public static readonly List<string> ValidStates = new();

        public Job(string name, int priority)
        {
            Name = name;
            _priority = priority;
        }

        public void Reset()
        {
            Name = string.Empty;
        }

        private static int ComputeHash(string input) => input.GetHashCode();
    }

    /// <summary>Internal helper class.</summary>
    internal class InternalHelper
    {
        public string Format(string value) => $"[{value}]";
    }

    public class GenericContainer<T> where T : class
    {
        public T Value { get; set; }
        public int Count { get; private set; }

        public void Add(T item) { Count++; }
    }
}
