use actix_web::{get, post, delete, web, HttpResponse, Responder};

#[get("/health")]
pub async fn health() -> impl Responder {
    HttpResponse::Ok().body("OK")
}

#[post("/api/items")]
pub async fn create_item() -> impl Responder {
    HttpResponse::Created().body("created")
}

#[delete("/api/items/{id}")]
pub async fn delete_item() -> impl Responder {
    HttpResponse::Ok().body("deleted")
}

pub async fn no_route_handler() -> impl Responder {
    HttpResponse::Ok().body("no route")
}
