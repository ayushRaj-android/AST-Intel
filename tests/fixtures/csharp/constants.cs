using System;
using System.Collections.Generic;

namespace TestProject.Utils
{
    public static class Constants
    {
        public const string AppName = "TestApp";
        public const int MaxRetries = 5;
        public const double Timeout = 30.0;
        internal const string InternalKey = "secret";

        public static readonly List<string> ValidRoles = new() { "admin", "user" };
    }

    using AliasType = System.Collections.Generic.Dictionary<string, int>;
}
