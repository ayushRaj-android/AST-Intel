/// Errors that can occur during processing.
#[derive(Debug)]
pub enum Error {
    /// Not found
    NotFound,
    /// IO error with message
    IoError(String),
    /// Network timeout with details
    Timeout { host: String, duration_ms: u64 },
}

/// Simple state machine.
enum State {
    Idle,
    Running,
    Paused(u32),
}
