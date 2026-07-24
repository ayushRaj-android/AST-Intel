/**
 * Fixture: TypeScript constants — tests ConstantNode extraction.
 */

export const MAX_SIZE: number = 100;
export const APP_NAME = "my-app";
const PRIVATE_CONST = 42;

let mutableVar = "hello";
var legacyVar = true;

// Destructured — these are tricky; we may not capture them individually
const { a, b } = { a: 1, b: 2 };
