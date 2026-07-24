using System.Runtime.Serialization;

namespace TestProject.Models.Enums
{
    /// <summary>Job execution status.</summary>
    public enum JobStatus
    {
        [EnumMember(Value = "NotStarted")]
        NotStarted = 1,

        [EnumMember(Value = "InProgress")]
        InProgress = 2,

        [EnumMember(Value = "Passed")]
        Passed = 3,

        [EnumMember(Value = "Failed")]
        Failed = 4,

        [EnumMember(Value = "Error")]
        Error = 5
    }

    public enum JobType
    {
        Sync,
        Async
    }

    internal enum Priority
    {
        Low = 0,
        Medium = 1,
        High = 2,
        Critical = 3
    }
}
