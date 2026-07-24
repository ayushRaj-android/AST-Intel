use std::fmt;

pub struct MyServer {
    pub host: String,
    port: u16,
}

/// Inherent implementation.
impl MyServer {
    /// Create a new server instance.
    pub fn new(host: String, port: u16) -> Self {
        Self { host, port }
    }

    /// Start listening.
    pub async fn start(&self) -> Result<(), Error> {
        todo!()
    }

    fn internal_setup(&mut self) {
        // private method
    }
}

/// Trait implementation: Display for MyServer.
impl fmt::Display for MyServer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.host, self.port)
    }
}

/// Generic impl.
impl<T: Clone> Container<T> {
    pub fn wrap(item: T) -> Self {
        Self { item }
    }
}
