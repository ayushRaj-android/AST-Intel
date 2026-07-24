/**
 * Fixture: TypeScript enums — tests EnumNode extraction.
 */

export enum Status {
    Active = "active",
    Inactive = "inactive",
    Pending = "pending",
}

enum Direction {
    Up,
    Down,
    Left,
    Right,
}

// Numeric enum with explicit values
export enum HttpStatus {
    OK = 200,
    NotFound = 404,
    InternalError = 500,
}

// Const enum
const enum Color {
    Red,
    Green,
    Blue,
}
