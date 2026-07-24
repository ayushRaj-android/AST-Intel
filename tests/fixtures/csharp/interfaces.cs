using System;
using System.Threading.Tasks;

namespace TestProject.Services
{
    /// <summary>Service contract for job management.</summary>
    public interface IJobService
    {
        Task<string> GetAsync(Guid id);
        void Process(string data);
        Task<bool> DeleteAsync(Guid id);
    }

    /// <summary>Generic repository interface.</summary>
    public interface IRepository<T> where T : class
    {
        Task<T> FindAsync(Guid id);
        Task<IEnumerable<T>> GetAllAsync();
        Task AddAsync(T entity);
    }

    /// <summary>Logging contract.</summary>
    public interface ILoggingService
    {
        void LogInfo(string message);
        void LogError(string message, Exception ex);
    }
}
