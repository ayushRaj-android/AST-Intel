// NOTE: This module handles authentication tokens
use std::collections::HashMap;

// TODO: Add support for refresh tokens
const MAX_RETRIES: u32 = 3;

struct TokenManager {
    tokens: HashMap<String, String>,
}

impl TokenManager {
    // HACK: Using unwrap here because the key is guaranteed to exist
    fn get_token(&self, key: &str) -> &str {
        self.tokens.get(key).unwrap()
    }

    // WHY: We clone the token because the borrow checker prevents
    // WHY: returning a reference to a value behind a mutex
    fn clone_token(&self, key: &str) -> String {
        self.tokens.get(key).unwrap().clone()
    }

    // SAFETY: Pointer offset is always within bounds because we checked length
    fn raw_bytes(&self) -> Vec<u8> {
        Vec::new()
    }
}

fn process_request(data: &[u8]) -> Result<(), String> {
    // FIXME: This silently drops malformed packets
    if data.is_empty() {
        return Ok(());
    }
    Ok(())
}

/* IMPORTANT: Changing this value requires updating the deployment config */
const BUFFER_SIZE: usize = 4096;

// PERF: Pre-allocating the vector avoids repeated heap allocations
fn batch_process(items: &[u32]) -> Vec<u32> {
    let mut results = Vec::with_capacity(items.len());
    for &item in items {
        results.push(item * 2);
    }
    results
}

// RATIONALE: We use a separate enum instead of bool flags
// RATIONALE: because the state machine has more than two states
enum ConnectionState {
    Connected,
    Disconnected,
    Reconnecting,
}
