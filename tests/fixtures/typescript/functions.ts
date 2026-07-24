/**
 * Fixture: TypeScript functions — tests FunctionNode extraction.
 */

export async function fetchData(url: string): Promise<Response> {
    return fetch(url);
}

function regularFn(x: number, y: number): number {
    return x + y;
}

// Exported arrow function
export const handler = async (req: Request): Promise<void> => {};

// Simple arrow function
const square = (x: number): number => x * x;

// Arrow function returning void (no return type annotation)
const logMessage = (msg: string) => {
    console.log(msg);
};

// Default export function
export default function defaultExport(): string {
    return "default";
}

// Generator function
function* counter(): Generator<number> {
    let i = 0;
    while (true) {
        yield i++;
    }
}

// Function with generic parameter
export function identity<T>(value: T): T {
    return value;
}
