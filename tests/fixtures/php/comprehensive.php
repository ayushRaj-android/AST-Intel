<?php
namespace App\Comprehensive;

use App\Models\BaseModel;
use App\Contracts\Cacheable;

const COMP_VERSION = '2.0';

interface Printable {
    public function print(): void;
}

abstract class BaseEntity {
    abstract public function getId(): int;
    public function exists(): bool { return true; }
}

trait Auditable {
    public function audit(): void {}
}

class Item extends BaseModel implements Cacheable, Printable {
    use Auditable;

    public string $title;
    public const TYPE = 'item';

    public function __construct(
        public readonly int $id,
    ) {}

    public function cacheKey(): string { return 'item_' . $this->id; }
    public function cacheTTL(): int { return 60; }
    public function print(): void {}
}

enum ItemStatus: string {
    case Draft = 'draft';
    case Published = 'published';
}

function formatItem(Item $item): string {
    return $item->title;
}
