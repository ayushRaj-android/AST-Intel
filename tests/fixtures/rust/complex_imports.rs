use crate::config::Settings as AppSettings;
use bytes::BytesMut;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use tokio::sync::mpsc::{self, Receiver, Sender};
use tracing::{debug, info, warn};

pub fn do_work() {
    let map = HashMap::new();
    let tree = BTreeMap::<String, i32>::new();
    let bm = BytesMut::with_capacity(256);
    HashMap::from([(1, 2)]);
}
