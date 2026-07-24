// A basic C struct with two fields.
struct Config {
    char* name;
    int port;
};

// A nested struct.
struct Server {
    struct Config config;
    int max_connections;
    float timeout;
};

// Typedef struct pattern.
typedef struct {
    double x;
    double y;
} Point;
