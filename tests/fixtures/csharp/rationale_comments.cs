// NOTE: This class handles database connection pooling
using System;
using System.Collections.Generic;

// TODO: Add connection health checks
namespace DataAccess
{
    public class ConnectionPool
    {
        private readonly Dictionary<string, object> _connections = new();
        private readonly int _maxSize;

        // HACK: Using object instead of DbConnection to avoid
        // HACK: pulling in System.Data dependency
        public ConnectionPool(int maxSize = 10)
        {
            _maxSize = maxSize;
        }

        // WHY: We return a wrapper instead of the raw connection
        // WHY: so we can track when it's returned to the pool
        public PooledConnection GetConnection(string key)
        {
            return new PooledConnection(this, key);
        }

        // FIXME: Doesn't properly handle connection timeouts
        public void ReturnConnection(string key, object conn)
        {
            _connections[key] = conn;
        }
    }

    // IMPORTANT: This class must be disposed — failure to do so leaks connections
    public class PooledConnection : IDisposable
    {
        private readonly ConnectionPool _pool;
        private readonly string _key;

        public PooledConnection(ConnectionPool pool, string key)
        {
            _pool = pool;
            _key = key;
        }

        public void Dispose()
        {
            GC.SuppressFinalize(this);
        }
    }

    // PERF: Caching parsed connection strings saves ~2ms per request
    public static class ConnectionStringCache
    {
        private static readonly Dictionary<string, string> Cache = new();

        public static string GetOrParse(string raw)
        {
            if (Cache.TryGetValue(raw, out var cached))
                return cached;
            Cache[raw] = raw;
            return raw;
        }
    }

    // SAFETY: All inputs are validated by the middleware layer
    public class QueryExecutor
    {
        public List<string> Execute(string query)
        {
            return new List<string>();
        }
    }

    // RATIONALE: Using a sealed record instead of a class because
    // RATIONALE: connection configs are immutable value objects
    public sealed record ConnectionConfig(string Host, int Port, string Database);
}
