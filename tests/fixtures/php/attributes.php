<?php

#[Entity]
#[Table("products")]
class Product {
    #[Column("name")]
    public string $name;

    #[Route("/api/products")]
    public function list(): array {
        return [];
    }
}
