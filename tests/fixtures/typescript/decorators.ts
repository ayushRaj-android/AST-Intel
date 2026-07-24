/**
 * Fixture: TypeScript decorators — tests attribute extraction.
 */

function Injectable() {
    return function (target: any) {};
}

function Log(target: any, key: string, descriptor: PropertyDescriptor) {}

@Injectable()
export class AppService {
    @Log
    process(): void {}

    @Log
    async fetchData(): Promise<void> {}
}
