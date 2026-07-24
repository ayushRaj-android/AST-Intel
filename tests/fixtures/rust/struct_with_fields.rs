/// A configuration settings struct.
#[derive(Debug, Clone, Deserialize)]
pub struct AppConfig {
    pub name: String,
    pub version: u32,
    workers: usize,
    pub(crate) secret: SecretString,
}

/// A tuple struct for wrapping types.
pub struct Wrapper(pub i32, String);

/// Unit struct with no fields.
pub struct Marker;

/// Generic struct with type parameters.
#[derive(Debug)]
pub struct Container<T, U>
where
    T: Clone,
    U: Default,
{
    pub items: Vec<T>,
    metadata: U,
}
