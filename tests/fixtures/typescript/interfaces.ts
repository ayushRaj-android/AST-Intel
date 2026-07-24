/**
 * Fixture: TypeScript interfaces — tests TraitNode extraction.
 */

export interface StorageHelper {
    retrieve(): Promise<Uint8Array>;
    store(data: string): void;
}

interface Serializable {
    serialize(): string;
    deserialize(raw: string): void;
}

// Interface with generics and extends
export interface Repository<T> extends Serializable {
    findById(id: string): Promise<T | null>;
    save(entity: T): Promise<void>;
}

// Interface with readonly properties
interface Config {
    readonly name: string;
    readonly port: number;
    debug?: boolean;
}

// Interface extending multiple
interface CombinedHelper extends StorageHelper, Serializable {
    version(): number;
}
