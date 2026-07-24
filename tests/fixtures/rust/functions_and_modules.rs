mod internal;
pub mod api;

use std::collections::HashMap;
use bytes::{Buf, BytesMut};
use tokio::sync::mpsc;
use crate::error::Error;

/// Custom result type.
pub type Result<T> = core::result::Result<T, Error>;

/// Public constant.
pub const MAX_RETRIES: u32 = 3;
static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Process incoming data asynchronously.
pub async fn process_data(
    input: &[u8],
    config: &AppConfig,
) -> Result<Vec<u8>> {
    let mut buf = BytesMut::with_capacity(1024);
    let map = HashMap::new();
    BytesMut::from(&input[..]);
    Ok(buf.to_vec())
}

/// A plain synchronous helper.
fn helper(x: i32) -> i32 {
    x * 2
}

/// Unsafe raw pointer manipulation.
pub unsafe fn dangerous_operation(ptr: *const u8) -> u8 {
    *ptr
}

macro_rules! my_macro {
    ($x:expr) => { $x + 1 };
}
