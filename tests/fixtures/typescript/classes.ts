/**
 * Fixture: TypeScript classes — tests StructNode and ImplBlockNode extraction.
 */

// Simple class with property declarations
export class DiskStorage {
    private name: string;
    public port: number;
    protected label: string;
    readonly id: string = "default";

    constructor(name: string, port: number) {
        this.name = name;
        this.port = port;
        this.label = "";
        this.id = "init";
    }

    async retrieve(): Promise<Uint8Array> {
        return new Uint8Array();
    }

    store(data: string): void {}

    static create(): DiskStorage {
        return new DiskStorage("disk", 8080);
    }

    get fullName(): string {
        return this.name;
    }
}

// Class with constructor parameter properties
export class UserService {
    constructor(
        private readonly name: string,
        public age: number,
        protected email: string,
        role: string,
    ) {}

    getName(): string {
        return this.name;
    }
}

// Class implements interface
class RedisStorage implements StorageHelper {
    async retrieve(): Promise<Uint8Array> {
        return new Uint8Array();
    }
    store(data: string): void {}
}

// Class extends another
class Base {
    baseMethod(): void {}
}

class Child extends Base {
    childMethod(): string {
        return "child";
    }
}

// Class extends and implements
class AdvancedStorage extends Base implements StorageHelper {
    async retrieve(): Promise<Uint8Array> {
        return new Uint8Array();
    }
    store(data: string): void {}
}

// Private (non-exported) class
class InternalHelper {
    help(): void {}
}

// Generic class
export class GenericBox<T> {
    value: T;

    constructor(value: T) {
        this.value = value;
    }

    getValue(): T {
        return this.value;
    }
}
