typedef unsigned int uint32;
typedef char* string_t;
typedef void (*callback_fn)(int, int);

#define MAX_SIZE 1024
#define VERSION "1.0.0"
#define ENABLE_LOGGING

enum Status {
    STATUS_OK,
    STATUS_ERROR,
    STATUS_PENDING
};

typedef enum {
    LOG_DEBUG,
    LOG_INFO,
    LOG_WARN,
    LOG_ERROR
} LogLevel;
