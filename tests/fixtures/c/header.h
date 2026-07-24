#ifndef HEADER_H
#define HEADER_H

struct Buffer {
    char* data;
    size_t length;
    size_t capacity;
};

void buffer_init(struct Buffer* buf, size_t capacity);
void buffer_free(struct Buffer* buf);
int buffer_append(struct Buffer* buf, const char* data, size_t len);

typedef int error_code;

#define BUFFER_DEFAULT_SIZE 256

#endif
