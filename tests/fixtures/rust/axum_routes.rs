use axum::{routing::{get, post, put, delete}, Router};
use std::sync::Arc;

pub struct AppState {
    pub db: Arc<String>,
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/health", get(health))
        .route("/api/users", get(list_users))
        .route("/api/users", post(create_user))
        .route("/api/users/{id}", put(update_user))
        .route("/api/users/{id}", delete(delete_user))
}

pub async fn health() -> &'static str {
    "OK"
}

pub async fn list_users() -> String {
    "[]".to_string()
}

pub async fn create_user() -> String {
    "created".to_string()
}

pub async fn update_user() -> String {
    "updated".to_string()
}

pub async fn delete_user() -> String {
    "deleted".to_string()
}
