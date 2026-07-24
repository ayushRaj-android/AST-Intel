use async_trait::async_trait;

/// A storage abstraction for key-value storage.
#[async_trait]
pub trait StorageHelper: Send + Sync {
    /// Required: get a value by key.
    async fn get(&self, key: &str) -> Result<Option<Vec<u8>>, Error>;
    /// Required: set a key-value pair.
    async fn set(&self, key: &str, value: &[u8]) -> Result<(), Error>;
    /// Default implementation for delete.
    fn delete(&self, key: &str) -> Result<(), Error> {
        Ok(())
    }
    /// An associated type.
    type Config;
    /// A constant bound.
    const MAX_KEY_LENGTH: usize;
}

/// A simple sync trait with no methods.
pub trait Marker {}

/// Trait with generics.
pub trait Converter<T> {
    fn convert(&self) -> T;
}
