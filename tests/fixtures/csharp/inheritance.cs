using System;
using System.Threading.Tasks;

namespace TestProject.Services
{
    /// <summary>Concrete service implementing IJobService.</summary>
    public class JobService : IJobService
    {
        private readonly IRepository<Job> _repository;
        private readonly ILoggingService _logger;

        public JobService(IRepository<Job> repository, ILoggingService logger)
        {
            _repository = repository;
            _logger = logger;
        }

        public async Task<string> GetAsync(Guid id)
        {
            var job = await _repository.FindAsync(id);
            return job?.Name ?? "Unknown";
        }

        public void Process(string data)
        {
            _logger.LogInfo($"Processing: {data}");
        }

        public async Task<bool> DeleteAsync(Guid id)
        {
            return await Task.FromResult(true);
        }

        private void ValidateInput(string data)
        {
            if (string.IsNullOrEmpty(data))
                throw new ArgumentException("Data cannot be empty");
        }
    }

    /// <summary>Logging service implementation.</summary>
    public class LoggingService : ILoggingService
    {
        public void LogInfo(string message)
        {
            Console.WriteLine($"INFO: {message}");
        }

        public void LogError(string message, Exception ex)
        {
            Console.WriteLine($"ERROR: {message} - {ex.Message}");
        }
    }
}
