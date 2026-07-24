// Reqwest HTTP client calls — test fixture for Feature 17.

use reqwest;

async fn fetch_users(client: &reqwest::Client) -> String {
    let resp = client.get("http://example.com/api/users")
        .send()
        .await
        .unwrap();
    resp.text().await.unwrap()
}

async fn create_user(client: &reqwest::Client) -> String {
    let resp = client.post("http://example.com/api/users")
        .json(&payload)
        .send()
        .await
        .unwrap();
    resp.text().await.unwrap()
}

async fn fetch_scoped() -> String {
    let resp = reqwest::get("http://example.com/api/health")
        .await
        .unwrap();
    resp.text().await.unwrap()
}

async fn dynamic_url(client: &reqwest::Client, base_url: &str) -> String {
    let url = format!("{}/api/jobs", base_url);
    let resp = client.post(&url)
        .send()
        .await
        .unwrap();
    resp.text().await.unwrap()
}

fn not_http() {
    let map = std::collections::HashMap::new();
    let _ = map.len();
}
