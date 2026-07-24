/**
 * Fixture: TypeScript abstract classes — tests TraitNode extraction.
 */

export abstract class Animal {
    abstract makeSound(): string;
    abstract get species(): string;

    move(distance: number): void {
        // concrete method
    }
}

class Dog extends Animal {
    makeSound(): string {
        return "Woof";
    }

    get species(): string {
        return "Canis familiaris";
    }
}

// Abstract generic class
abstract class Container<T> {
    abstract getValue(): T;
    abstract setValue(value: T): void;

    isEmpty(): boolean {
        return false;
    }
}
