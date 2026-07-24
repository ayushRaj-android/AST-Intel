package comments

// Exported constant with doc comment.
const Version = "1.0.0"

// Logger is a structured logger.
// It supports multiple output formats.
type Logger struct {
	// Level controls the minimum log level.
	Level string
	// Output is the destination writer.
	Output string
}

// NewLogger creates a new Logger instance.
// It initializes with sane defaults.
func NewLogger(level string) *Logger {
	return &Logger{Level: level, Output: "stdout"}
}

// LogEntry represents a single log entry.
type LogEntry interface {
	// Message returns the log message.
	Message() string
	// Timestamp returns the creation time as Unix epoch.
	Timestamp() int64
}
