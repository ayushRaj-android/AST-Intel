/**
 * Fixture: TypeScript type aliases — tests TypeAliasNode and StructNode extraction.
 */

// Simple alias → TypeAliasNode
type StringAlias = string;

// Union → TypeAliasNode
export type Result<T> = T | Error;

// Function type → TypeAliasNode
type Callback = (x: number) => void;

// Intersection → TypeAliasNode
type Named = { name: string } & { id: number };

// Object type alias → StructNode (has named fields)
export type UserConfig = {
    name: string;
    port: number;
    debug?: boolean;
};

// Simple object type
type Point = {
    x: number;
    y: number;
};
