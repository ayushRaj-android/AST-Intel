# Roadmap: Code History Intelligence

> **Status**: Draft  
> **Created**: 2026-05-15  
> **Scope**: Phases 1–4, from standalone VS Code extension to full AST-Intel MCP integration  
> **Principle**: Every claim about code must cite its source — commit, PR, review, or document.

---

## Executive Summary

Code History Intelligence gives developers and SWE agents access to a codebase's **lived history**: who owns a function, why it was written, what PRs changed it, what decisions shaped it, and what incidents affected it.

**Phased delivery:**

| Phase | Name | Focus | Depends On |
|-------|------|-------|------------|
| 1 | Foundation | Git-native history & ownership (pure `git`) | — |
| 2 | Provider-Enriched History | GitHub/ADO/Bitbucket/CodeCommit PR + review data | Phase 1 |
| 3 | MCP Integration | Expose tools via AST-Intel MCP server | Phase 2, AST-Intel core |
| 4 | Rich Signals | RFCs, incidents, tribal knowledge, confidence scoring | Phase 3 |

Phases 1–2 ship as a **standalone VS Code extension**. Phases 3–4 integrate into the **AST-Intel MCP server** so any MCP-aware agent (Copilot, Claude, Cursor) can query history programmatically.

---

## Phase 1: Foundation — Git-Native History & Ownership

### Goals

1. Extract per-symbol commit history using `git log -L` and `git blame`
2. Compute ownership scores from commit frequency, recency, and line-count
3. Ship a VS Code extension MVP with a "Who Owns This?" panel
4. Zero provider API dependencies — pure local git

### Components to Build

```
code-history-intel/
├── src/
│   ├── extension.ts              # VS Code activation, command registration
│   ├── git/
│   │   ├── blame.ts              # git blame parser
│   │   ├── log.ts                # git log -L parser (line-range history)
│   │   ├── diff.ts               # git diff stat extraction
│   │   └── types.ts              # GitCommit, BlameLine types
│   ├── history/
│   │   ├── symbolResolver.ts     # Map cursor position → symbol name + span
│   │   ├── historyBuilder.ts     # Aggregate commits for a symbol
│   │   └── types.ts              # HistoryRecord
│   ├── ownership/
│   │   ├── ownershipScorer.ts    # Weighted scoring algorithm
│   │   └── types.ts              # OwnershipRecord, OwnershipScore
│   ├── cache/
│   │   └── historyCache.ts       # LRU + file-watcher invalidation
│   └── views/
│       ├── ownershipPanel.ts     # "Who Owns This?" webview
│       ├── historyTimeline.ts    # Commit timeline for a symbol
│       └── codeLens.ts           # Inline ownership annotations
├── package.json
├── tsconfig.json
└── test/
    ├── git/blame.test.ts
    ├── git/log.test.ts
    ├── ownership/scorer.test.ts
    └── fixtures/                  # Sample git output for deterministic tests
```

**Complexity**: **L**

### Key Data Models

#### TypeScript (Extension side)

```typescript
// --- Git layer ---

interface GitCommit {
  hash: string;
  shortHash: string;
  author: string;
  authorEmail: string;
  date: Date;
  message: string;
  filesChanged: number;
  insertions: number;
  deletions: number;
}

interface BlameLine {
  lineNumber: number;
  commit: GitCommit;
  originalLineNumber: number;
  content: string;
}

interface BlameHunk {
  startLine: number;
  endLine: number;
  commit: GitCommit;
  lines: BlameLine[];
}

// --- History layer ---

interface SymbolSpan {
  filePath: string;
  startLine: number;
  endLine: number;
  symbolName: string;
  symbolKind: "function" | "method" | "class" | "interface" | "type" | "constant";
  language: string;
}

interface HistoryRecord {
  symbol: SymbolSpan;
  commits: GitCommit[];
  firstSeen: Date;
  lastModified: Date;
  totalCommits: number;
  /**
   * Blame snapshot at HEAD — which commit owns each line range.
   */
  currentBlame: BlameHunk[];
}

// --- Ownership layer ---

interface OwnershipScore {
  author: string;
  authorEmail: string;
  /**
   * Composite score 0.0–1.0, combining:
   * - commitFrequency (40%): fraction of total commits to this symbol
   * - recency (35%): exponential decay from last commit date
   * - lineOwnership (25%): fraction of current lines authored (blame)
   */
  score: number;
  commitCount: number;
  lastCommitDate: Date;
  linesOwned: number;
  totalLines: number;
}

interface OwnershipRecord {
  symbol: SymbolSpan;
  owners: OwnershipScore[];        // sorted descending by score
  primaryOwner: OwnershipScore;    // owners[0]
  computedAt: Date;
  gitHead: string;                 // SHA at which ownership was computed
}
```

#### Python (Shared retrieval engine — built now, reused in Phase 3)

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SymbolKind(StrEnum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    TYPE = "type"
    CONSTANT = "constant"


@dataclass(frozen=True, slots=True)
class SymbolRef:
    """Pointer to a symbol in the codebase."""
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_kind: SymbolKind
    language: str


@dataclass(frozen=True, slots=True)
class GitCommitInfo:
    hash: str
    short_hash: str
    author: str
    author_email: str
    date: datetime
    message: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass(frozen=True, slots=True)
class BlameHunk:
    start_line: int
    end_line: int
    commit: GitCommitInfo


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    symbol: SymbolRef
    commits: tuple[GitCommitInfo, ...]
    first_seen: datetime
    last_modified: datetime
    total_commits: int
    current_blame: tuple[BlameHunk, ...] = ()


@dataclass(frozen=True, slots=True)
class OwnershipScore:
    author: str
    author_email: str
    score: float                   # 0.0–1.0 composite
    commit_count: int
    last_commit_date: datetime
    lines_owned: int
    total_lines: int


@dataclass(frozen=True, slots=True)
class OwnershipRecord:
    symbol: SymbolRef
    owners: tuple[OwnershipScore, ...]
    primary_owner: OwnershipScore | None    # None when no commits touch the symbol (e.g. new file)
    computed_at: datetime
    git_head: str
```

### Ownership Scoring Algorithm

```
score(author, symbol) =
    0.40 × (commits_by_author / total_commits)
  + 0.35 × exp(-λ × days_since_authors_last_commit)   # λ = 0.005 (~140-day half-life)
  + 0.25 × (blame_lines_by_author / total_lines)
```

`days_since_authors_last_commit` is measured **per author** (days since that author's most recent commit touching the symbol), not the symbol's overall last-modified date — otherwise every contributor would receive the same recency score.

Weights are configurable via extension settings. The exponential decay prevents long-departed authors from dominating.

### Symbol Resolution Strategy

Phase 1 uses VS Code's built-in `DocumentSymbolProvider` to resolve cursor position → symbol span. This avoids duplicating AST-Intel's parser logic in TypeScript and works for all languages VS Code supports. In Phase 3, this is replaced by AST-Intel's `GraphNode` lookup.

Fallback chain:
1. `vscode.commands.executeCommand('vscode.executeDocumentSymbolProvider', uri)`
2. If no symbol at cursor → expand to enclosing function via tree-sitter WASM (bundled for top 5 languages)
3. If still nothing → fall back to `git log -L <line>,<line>:<file>` on the raw line range

### Cache Strategy

| Key | Value | TTL | Invalidation |
|-----|-------|-----|-------------|
| `blame:{file}:{HEAD}` | `BlameHunk[]` | ∞ (keyed by HEAD) | New HEAD on `git` ops |
| `history:{symbol_id}:{HEAD}` | `HistoryRecord` | 10 min | File save + HEAD change |
| `ownership:{symbol_id}:{HEAD}` | `OwnershipRecord` | 10 min | Same |

Cache is in-memory LRU (max 500 entries). A `FileSystemWatcher` on `.git/HEAD` and `.git/refs/` triggers bulk invalidation.

### VS Code Extension MVP UX

**Commands:**
- `codeHistoryIntel.whoOwns` — Open "Who Owns This?" panel for symbol at cursor
- `codeHistoryIntel.symbolHistory` — Open timeline view for symbol at cursor
- `codeHistoryIntel.toggleCodeLens` — Toggle inline ownership CodeLens

**"Who Owns This?" Panel:**
```
┌─────────────────────────────────────────────────┐
│  Who Owns: processPayment()                     │
│  src/billing/processor.ts:42-87                 │
├─────────────────────────────────────────────────┤
│  👤 Primary Owner                               │
│  Alice Chen (alice@company.com)                 │
│  Score: 0.82 │ 14 commits │ Last: 3 days ago   │
│  Owns 67/87 lines (77%)                        │
├─────────────────────────────────────────────────┤
│  📊 Other Contributors                          │
│  Bob Park — 0.31 │ 5 commits │ 2 weeks ago     │
│  Carol Wu — 0.12 │ 2 commits │ 4 months ago    │
├─────────────────────────────────────────────────┤
│  📅 Recent Activity                             │
│  a1b2c3d  Fix decimal rounding      3 days ago  │
│  e4f5g6h  Add retry logic           1 week ago  │
│  i7j8k9l  Extract payment helper    2 weeks ago │
└─────────────────────────────────────────────────┘
```

### Success Metrics

| Metric | Target | How Measured |
|--------|--------|-------------|
| Blame parse latency (cold, 1k-line file) | < 500ms | Internal timer |
| Ownership computation (single symbol) | < 200ms | Internal timer |
| Cache hit rate after warm-up | > 80% | Counter telemetry |
| Correct primary owner (manual audit on 3 OSS repos) | > 85% accuracy | Manual comparison with CODEOWNERS + recent activity |
| Extension activation time | < 300ms | VS Code built-in |

### Risk Factors

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `git log -L` is slow on large repos (>100k commits) | Medium | High | Use `--max-count`, limit date range, cache aggressively |
| Symbol resolution misses renamed functions | Medium | Medium | Fall back to line-range blame; track renames in Phase 2 |
| Monorepo with shallow clones missing history | Low | High | Detect shallow clone, show warning, suggest `git fetch --unshallow` |
| Author email aliasing (same person, multiple emails) | High | Medium | Use `.mailmap` if present; add manual alias config in Phase 2 |

### ADR-001: Pure Git for Phase 1 (No Provider APIs)

#### Context
We need to decide whether Phase 1 should immediately integrate GitHub/ADO APIs for PR data, or rely solely on local git operations.

#### Decision
Phase 1 uses **only local git** (`git blame`, `git log -L`, `git diff`). No network calls, no API tokens, no provider-specific code.

#### Consequences

**Positive:**
- Works offline, in air-gapped environments, on any git host
- No authentication complexity; zero configuration needed
- Faster iteration — ship the extension sooner
- Forces a clean separation between git-native and provider-enriched data

**Negative:**
- Cannot show PR titles, review comments, or approval status
- Commit messages are the only context signal (often low quality)
- No way to distinguish "who reviewed" from "who committed"

**Alternatives Considered:**
- **GitHub API from day 1**: Richer data, but locks out ADO/Bitbucket users and adds auth complexity before the core is proven
- **Hybrid (git + optional GitHub)**: Increases surface area; decided to defer to Phase 2 where the adapter pattern is properly designed

**Status:** Accepted

---

## Phase 2: Provider-Enriched History

### Goals

1. Design and implement a `HistoryProvider` adapter interface supporting GitHub, ADO, Bitbucket, and AWS CodeCommit
2. Link PR metadata (title, description, review comments, approvals) to per-symbol history
3. Extend the VS Code extension with "evidence cards" — rich context cards citing the source, date, author, and provider
4. Ship GitHub adapter as GA; ADO adapter as beta; Bitbucket and CodeCommit as stubs

### Dependencies on Phase 1

- `HistoryRecord` and `OwnershipRecord` schemas (extended, not replaced)
- Git blame/log infrastructure (used to map commits → PRs)
- Symbol resolution and caching layer
- Extension scaffold (commands, panels, CodeLens)

### Components to Build

```
code-history-intel/
├── src/
│   ├── providers/
│   │   ├── types.ts               # HistoryProvider interface, PullRequestInfo, ReviewComment
│   │   ├── registry.ts            # Auto-detect provider from git remote URL
│   │   ├── github/
│   │   │   ├── adapter.ts         # GitHubHistoryProvider implements HistoryProvider
│   │   │   ├── graphql.ts         # GitHub GraphQL queries (PRs, reviews, commits)
│   │   │   └── auth.ts            # GitHub token via VS Code auth API or env
│   │   ├── ado/
│   │   │   ├── adapter.ts         # ADOHistoryProvider implements HistoryProvider
│   │   │   ├── restClient.ts      # ADO REST API client (PRs, work items, threads)
│   │   │   └── auth.ts            # PAT or Azure Identity
│   │   ├── bitbucket/
│   │   │   ├── adapter.ts         # BitbucketHistoryProvider (stub → beta)
│   │   │   └── auth.ts
│   │   └── codecommit/
│   │       ├── adapter.ts         # CodeCommitHistoryProvider (stub)
│   │       └── auth.ts            # AWS credentials chain
│   ├── enrichment/
│   │   ├── prLinker.ts            # Map commit SHA → PR(s) via provider API
│   │   ├── reviewExtractor.ts     # Extract review comments touching symbol's lines
│   │   └── enrichedHistory.ts     # Merge git-native + provider data
│   └── views/
│       ├── evidenceCard.ts        # Rich card component with citation
│       └── providerBadge.ts       # Visual indicator of data source
├── test/
│   ├── providers/github.test.ts
│   ├── providers/ado.test.ts
│   ├── enrichment/prLinker.test.ts
│   └── fixtures/                  # Mock API responses
```

**Complexity**: **XL** — GitHub adapter is L, ADO adapter is L, shared interface + commit→PR linking is M.

### Key Data Models

#### TypeScript

```typescript
// --- Provider abstraction ---

interface ProviderCapabilities {
  pullRequests: boolean;
  reviewComments: boolean;
  reviewApprovals: boolean;
  commitToPrMapping: boolean;
  fileChangesInPr: boolean;
  workItemLinking: boolean;       // ADO-specific
  pipelineStatus: boolean;
}

interface ProviderIdentity {
  provider: "github" | "ado" | "bitbucket" | "codecommit";
  remoteUrl: string;
  owner: string;                   // org or user
  repo: string;
  project?: string;                // ADO project
}

interface PullRequestInfo {
  id: number | string;
  title: string;
  description: string;
  url: string;
  state: "open" | "merged" | "closed";
  author: string;
  authorEmail: string;
  createdAt: Date;
  mergedAt?: Date;
  mergeCommit?: string;
  baseBranch: string;
  headBranch: string;
  labels: string[];
}

interface ReviewComment {
  id: string;
  prId: number | string;
  author: string;
  body: string;
  path: string;                    // file path in the diff
  line?: number;                   // diff line, if available
  createdAt: Date;
  state: "comment" | "approved" | "changes_requested" | "dismissed";
}

interface ReviewThread {
  comments: ReviewComment[];
  resolved: boolean;
  resolvedBy?: string;
}

/**
 * Core adapter interface. Every provider implements this.
 * Methods return null when the capability is not supported.
 */
interface HistoryProvider {
  readonly identity: ProviderIdentity;
  readonly capabilities: ProviderCapabilities;

  /**
   * Initialize the provider (validate auth, check API access).
   * Throws if authentication fails.
   */
  initialize(): Promise<void>;

  /**
   * Find PRs that contain the given commit SHA.
   * Most providers support this via search/association APIs.
   */
  getPullRequestsForCommit(sha: string): Promise<PullRequestInfo[]>;

  /**
   * Get review comments on a PR that touch lines within the given range.
   * Returns all review threads if path/line filtering is not supported.
   */
  getReviewComments(
    prId: number | string,
    filePath?: string,
    startLine?: number,
    endLine?: number,
  ): Promise<ReviewThread[]>;

  /**
   * Batch-resolve commit SHAs to PRs (for efficiency).
   * Default implementation loops getPullRequestsForCommit; providers
   * can override with bulk APIs.
   */
  batchGetPullRequests?(shas: string[]): Promise<Map<string, PullRequestInfo[]>>;
}

// --- Enriched history (extends Phase 1 HistoryRecord) ---

interface EnrichedCommit extends GitCommit {
  pullRequests: PullRequestInfo[];
  reviewThreads: ReviewThread[];
}

interface EnrichedHistoryRecord extends HistoryRecord {
  enrichedCommits: EnrichedCommit[];
  /**
   * The "decision context" — why changes were made, derived from
   * PR descriptions + review comments.
   */
  decisionSignals: DecisionSignal[];
}

interface DecisionSignal {
  source: "pr_description" | "review_comment" | "commit_message";
  text: string;
  author: string;
  date: Date;
  url?: string;                    // deeplink to PR or comment
  prId?: number | string;
}

// --- Evidence card (UI model) ---

interface EvidenceCard {
  symbolName: string;
  evidenceType: "ownership" | "history" | "review" | "decision";
  title: string;
  body: string;
  source: {
    type: "git_blame" | "git_log" | "pr" | "review_comment";
    provider?: ProviderIdentity["provider"];
    url?: string;
    date: Date;
    author: string;
  };
  confidence: number;              // 0.0–1.0
}
```

#### Python (Shared retrieval engine)

```python
@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    id: str
    title: str
    description: str
    url: str
    state: str                     # "open" | "merged" | "closed"
    author: str
    author_email: str
    created_at: datetime
    merged_at: datetime | None = None
    merge_commit: str | None = None
    base_branch: str = ""
    head_branch: str = ""
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewComment:
    id: str
    pr_id: str
    author: str
    body: str
    path: str
    line: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    state: str = "comment"          # "comment" | "approved" | "changes_requested"


@dataclass(frozen=True, slots=True)
class DecisionSignal:
    source: str                     # "pr_description" | "review_comment" | "commit_message"
    text: str
    author: str
    date: datetime
    url: str = ""
    pr_id: str = ""


@dataclass(frozen=True, slots=True)
class EnrichedHistoryRecord:
    """Extends HistoryRecord with provider-sourced PR and review data."""
    history: HistoryRecord
    pull_requests: tuple[PullRequestInfo, ...] = ()
    review_comments: tuple[ReviewComment, ...] = ()
    decision_signals: tuple[DecisionSignal, ...] = ()
```

### Provider Auto-Detection

The `ProviderRegistry` inspects `git remote -v` output and pattern-matches:

| Remote URL Pattern | Provider |
|---|---|
| `github.com` or `github.dev` | GitHub |
| `dev.azure.com` or `*.visualstudio.com` | Azure DevOps |
| `bitbucket.org` | Bitbucket |
| `git-codecommit.*.amazonaws.com` | AWS CodeCommit |

If multiple remotes exist, the user is prompted. The selected provider is cached in workspace settings.

### Provider Capability Matrix

| Capability | GitHub | Azure DevOps | Bitbucket | CodeCommit |
|---|---|---|---|---|
| Commit → PR mapping | ✅ GraphQL `associatedPullRequests` | ✅ REST `commits/{sha}/pullRequests` | ✅ REST `commit/{sha}/pullrequests` | ❌ No API |
| PR metadata | ✅ Full | ✅ Full | ✅ Full | ⚠️ Title only |
| Line-level review comments | ✅ `pullRequestReviewThreads` | ✅ `threads` API with `threadContext` | ✅ `comments` with `inline` | ❌ |
| Review approval status | ✅ `reviews` | ✅ `reviewers` with vote | ✅ `participants` with approval | ❌ |
| Work item / issue linking | ⚠️ Linked issues (limited) | ✅ Native work item links | ✅ Jira links | ❌ |
| Batch commit → PR | ✅ GraphQL batching | ⚠️ Sequential REST | ⚠️ Sequential REST | ❌ |

**Legend:** ✅ Supported, ⚠️ Partial / degraded, ❌ Not available

### Commit → PR Linking Strategy

The enrichment pipeline works in three stages:

1. **Batch resolve**: Take all commit SHAs from `HistoryRecord.commits`, call `batchGetPullRequests` → `Map<SHA, PullRequestInfo[]>`
2. **Filter relevant reviews**: For each PR, call `getReviewComments(prId, filePath, startLine, endLine)` to get only threads touching the symbol's lines
3. **Synthesize decision signals**: Extract text snippets from PR descriptions and review comments, tag with source type

Rate limiting: GitHub GraphQL has 5,000 points/hour. Each `batchGetPullRequests` call costs ~2 points per 100 commits. A symbol with 50 commits costs 1 point. ADO has 200 requests/min (per PAT). Batch calls are capped at 30 SHAs per request to stay well within limits.

### Extension UX: Evidence Cards

```
┌─────────────────────────────────────────────────────┐
│  📋 processPayment() — Decision History             │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─ PR #1247 ────────────────────────────────────┐  │
│  │ "Add idempotency key to payment processing"   │  │
│  │ Alice Chen · merged 3 days ago · GitHub       │  │
│  │                                               │  │
│  │ 💬 Review: "We should use the Stripe          │  │
│  │    idempotency key, not a custom UUID"        │  │
│  │    — Bob Park, approved                       │  │
│  │                                               │  │
│  │ [Open PR ↗]                                   │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ┌─ PR #1189 ────────────────────────────────────┐  │
│  │ "Extract billing module from monolith"        │  │
│  │ Carol Wu · merged 2 weeks ago · GitHub        │  │
│  │                                               │  │
│  │ 💬 Review: "processPayment should be in its   │  │
│  │    own file — it's the hottest code path"     │  │
│  │    — Alice Chen, approved                     │  │
│  │                                               │  │
│  │ [Open PR ↗]                                   │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ┌─ Blame ───────────────────────────────────────┐  │
│  │ Lines 42-55: Alice Chen (a1b2c3d, 3 days)    │  │
│  │ Lines 56-72: Alice Chen (e4f5g6h, 1 week)    │  │
│  │ Lines 73-87: Bob Park (i7j8k9l, 2 weeks)     │  │
│  │ Source: git blame (local)                     │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

Every card shows a **source badge** (git blame, GitHub PR, ADO PR, etc.) so the user always knows provenance.

### Authentication Strategy

| Provider | Primary Method | Fallback |
|---|---|---|
| GitHub | VS Code built-in GitHub auth (`vscode.authentication.getSession('github')`) | `GITHUB_TOKEN` env var |
| Azure DevOps | Azure Identity via `@azure/identity` | `AZURE_DEVOPS_PAT` env var |
| Bitbucket | App password via settings | `BITBUCKET_TOKEN` env var |
| CodeCommit | AWS SDK credential chain (`~/.aws/credentials`) | IAM role (EC2/ECS) |

Tokens are never stored by the extension — always delegated to the OS credential store or VS Code's `SecretStorage`.

### Success Metrics

| Metric | Target | How Measured |
|--------|--------|-------------|
| Commit → PR resolution rate (GitHub) | > 95% of merge commits map to a PR | Automated test on 5 OSS repos |
| Commit → PR resolution rate (ADO) | > 90% | Automated test on 2 ADO repos |
| Review comment relevance (symbol-scoped) | > 80% of returned comments are about the symbol | Manual audit |
| Enrichment latency (10 commits, cached auth) | < 2s (GitHub), < 3s (ADO) | Internal timer |
| Evidence card completeness | Every card has source, date, author | UI test |

### Risk Factors

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| GitHub API rate limits hit by active users | Medium | High | GraphQL batching, aggressive caching (PR data is immutable after merge), exponential backoff |
| ADO REST API pagination complexity | Medium | Medium | Abstract pagination in `restClient.ts`; use `$top` + continuation tokens consistently |
| Squash merges lose commit → PR mapping | High | Medium | Fall back to matching commit message patterns (`(#1234)`) and PR search by date range |
| Review comments on file-level (not line-level) | Medium | Low | Include file-level comments with lower confidence score; let user filter |
| Auth token expiration mid-session | Medium | Medium | Catch 401, re-trigger auth flow, retry once |

### ADR-002: Adapter Pattern with Capability Matrix

#### Context
We need to support 4 git hosting providers with different API capabilities. We must decide between: (a) a lowest-common-denominator interface, (b) a capability-aware adapter pattern, or (c) provider-specific code paths in the core.

#### Decision
Use a **capability-aware adapter pattern**: a single `HistoryProvider` interface where each method can declare support via a `ProviderCapabilities` struct. Callers check capabilities before calling optional methods.

#### Consequences

**Positive:**
- Core enrichment logic is provider-agnostic
- New providers are added by implementing one interface
- Graceful degradation — the UI shows what's available, grays out what isn't
- Easy to test with mock providers

**Negative:**
- More complex than a flat interface — callers must check capabilities
- Risk of "capability sprawl" as providers add features

**Alternatives Considered:**
- **Lowest-common-denominator**: Would reduce GitHub/ADO to CodeCommit's level (almost nothing). Rejected — too limiting.
- **Provider-specific code paths**: Would scatter `if (github)` throughout the codebase. Rejected — unmaintainable.

**Status:** Accepted

### ADR-003: GraphQL for GitHub, REST for Others

#### Context
GitHub offers both REST and GraphQL APIs. ADO, Bitbucket, and CodeCommit are REST-only.

#### Decision
Use **GitHub GraphQL API** for PR and review queries (allows batch resolution of commit→PR in a single call). Use REST for all other providers.

#### Consequences

**Positive:**
- 10–50x fewer HTTP requests for GitHub (batch commit→PR resolution)
- Can fetch PR + reviews + comments in a single query
- Lower rate limit consumption (GraphQL uses point-based limits, not request-based)

**Negative:**
- GraphQL queries are harder to debug than REST
- Need to handle GraphQL-specific errors (partial data, query complexity limits)

**Status:** Accepted

---

## Phase 3: MCP Integration

### Goals

1. Expose `get_symbol_history`, `get_ownership`, and `get_decision_context` as MCP tools in the AST-Intel server
2. Extract a **shared retrieval engine** (Python) used by both the VS Code extension (via subprocess/sidecar) and the MCP server
3. Plug into AST-Intel's existing `GraphNode` → symbol resolution, eliminating the VS Code `DocumentSymbolProvider` dependency for MCP consumers
4. All responses use a **citation-first format** — every claim has a traceable source

### Dependencies on Prior Phases

- Phase 1: Git blame/log parsing, ownership scoring algorithm, `HistoryRecord`/`OwnershipRecord` schemas
- Phase 2: `HistoryProvider` adapter pattern, `EnrichedHistoryRecord`, `DecisionSignal`
- AST-Intel core: `GraphNode`, `NodeKind`, `QueryEngine`, `GraphBuilder`, `ExtractorBase`

### Components to Build

```
ast_intel/
├── history/                        # NEW — shared retrieval engine
│   ├── __init__.py
│   ├── git_ops.py                  # git blame, git log -L, git diff wrappers
│   ├── history_builder.py          # Build HistoryRecord for a symbol
│   ├── ownership_scorer.py         # Compute OwnershipRecord
│   ├── provider_registry.py        # Detect provider from remote URL
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py                 # HistoryProvider abstract class
│   │   ├── github_provider.py      # GitHub GraphQL adapter
│   │   ├── ado_provider.py         # ADO REST adapter
│   │   ├── bitbucket_provider.py   # Bitbucket REST adapter
│   │   └── codecommit_provider.py  # CodeCommit adapter
│   ├── enrichment.py               # Merge git-native + provider data
│   ├── models.py                   # All dataclasses (HistoryRecord, etc.)
│   └── cache.py                    # File-backed LRU with git HEAD keying
├── core/
│   └── _query_engine.py            # MODIFIED — add history query methods
├── mcp_server.py                   # MODIFIED — register new tools
```

**Complexity**: **XL** — retrieval engine extraction is L, MCP tool wiring is M, AST-Intel graph integration is L.

### How It Plugs Into AST-Intel's Architecture

AST-Intel's current flow:

```
Source Files → Extractors → FileAST → Indexer → WorkspaceAST → GraphBuilder → CodeGraph → QueryEngine
```

Code History Intelligence adds a **parallel data layer** that enriches `GraphNode` with temporal/social metadata:

```
CodeGraph.nodes ──┐
                  ├──→ HistoryBuilder.build(node) ──→ HistoryRecord
git repo ─────────┘

HistoryRecord ──────┐
                    ├──→ Enrichment.enrich(record) ──→ EnrichedHistoryRecord
ProviderAdapter ────┘
```

**Integration point**: `QueryEngine` gets new methods that accept a `GraphNode.id` (or symbol name + file), resolve the symbol's `Span`, and delegate to `HistoryBuilder` + `Enrichment`.

```python
# In QueryEngine (extended)

class QueryEngine:
    def __init__(
        self,
        graph: CodeGraph,
        analysis: GraphAnalysis | None = None,
        history_builder: HistoryBuilder | None = None,    # NEW
    ) -> None:
        ...
        self._history = history_builder

    def get_symbol_history(self, symbol_id: str) -> HistoryRecord | None:
        """Get git history for a symbol by its graph node ID."""
        node = self._node_map.get(symbol_id)
        if node is None or node.file is None or node.span is None:
            return None
        return self._history.build(
            file_path=node.file,
            start_line=node.span.start_line,
            end_line=node.span.end_line,
            symbol_name=node.label,
            symbol_kind=_node_kind_to_symbol_kind(node.kind),
        )

    def get_ownership(self, symbol_id: str) -> OwnershipRecord | None:
        """Get ownership for a symbol by its graph node ID."""
        history = self.get_symbol_history(symbol_id)
        if history is None:
            return None
        return self._history.compute_ownership(history)

    def get_decision_context(
        self, symbol_id: str, *, max_signals: int = 10,
    ) -> list[DecisionSignal]:
        """Get decision context (PR descriptions, review comments) for a symbol."""
        history = self.get_symbol_history(symbol_id)
        if history is None:
            return []
        enriched = self._history.enrich(history)
        return list(enriched.decision_signals[:max_signals])
```

### MCP Tool Definitions

Three new tools registered in `mcp_server.py`:

```python
# --- Tool: get_symbol_history ---
Tool(
    name="get_symbol_history",
    description=(
        "Get the git commit history for a specific code symbol (function, class, method). "
        "Returns chronological list of commits that modified the symbol, including "
        "author, date, message, and associated PRs if available. "
        "Use this to understand how and when a piece of code evolved."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol name or fully-qualified ID (e.g. 'processPayment' or 'src/billing/processor.ts::processPayment')",
            },
            "max_commits": {
                "type": "integer",
                "description": "Maximum number of commits to return (default: 20)",
                "default": 20,
            },
        },
        "required": ["symbol"],
    },
)

# --- Tool: get_ownership ---
Tool(
    name="get_ownership",
    description=(
        "Identify who owns a code symbol — who wrote it, who maintains it, and who "
        "has changed it most recently. Returns ranked ownership scores based on "
        "commit frequency, recency, and line-level blame attribution. "
        "Use this to find the right person to review a change or ask about intent."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol name or fully-qualified ID",
            },
        },
        "required": ["symbol"],
    },
)

# --- Tool: get_decision_context ---
Tool(
    name="get_decision_context",
    description=(
        "Retrieve the decision context for a code symbol — why it was written, "
        "what PRs introduced or changed it, and what review discussions shaped it. "
        "Every returned signal includes a citation (PR URL, commit SHA, or review link). "
        "Use this before modifying unfamiliar code to understand existing design intent."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol name or fully-qualified ID",
            },
            "max_signals": {
                "type": "integer",
                "description": "Maximum decision signals to return (default: 10)",
                "default": 10,
            },
        },
        "required": ["symbol"],
    },
)
```

### Citation-First Response Format

Every MCP tool response embeds citations. Agents can relay these to the user or use them to verify claims.

#### Symbol resolution & disambiguation

The `symbol` input accepts three forms, resolved in order:

1. A fully-qualified `GraphNode.id` (exact lookup via `_node_map`)
2. A `file::name` form (e.g. `src/billing/processor.ts::processPayment`) resolved via `_suffix_index`
3. A bare label (e.g. `processPayment`) resolved via `_label_index`

If form (3) matches multiple nodes, the underlying `QueryEngine` raises `AmbiguousSymbolError`. The MCP tools catch this and return a structured response listing all candidate `GraphNode.id`s so the agent can re-call with a fully-qualified ID:

```json
{
  "error": "ambiguous_symbol",
  "symbol": "processPayment",
  "candidates": [
    "src/billing/processor.ts::processPayment",
    "src/legacy/billing.ts::processPayment"
  ]
}
```

This preserves the citation-first contract — the agent never gets a guess; it gets either a resolved answer or an explicit disambiguation request.

```json
{
  "symbol": "src/billing/processor.ts::processPayment",
  "ownership": {
    "primary_owner": {
      "author": "Alice Chen",
      "score": 0.82,
      "citation": {
        "type": "git_blame",
        "detail": "67/87 lines authored at HEAD (a1b2c3d)"
      }
    }
  },
  "decision_context": [
    {
      "signal": "Idempotency key added to prevent duplicate charges",
      "source": "pr_description",
      "citation": {
        "type": "github_pr",
        "pr_id": 1247,
        "url": "https://github.com/org/repo/pull/1247",
        "author": "Alice Chen",
        "date": "2025-05-12"
      }
    },
    {
      "signal": "Use Stripe idempotency key, not custom UUID",
      "source": "review_comment",
      "citation": {
        "type": "github_review",
        "pr_id": 1247,
        "url": "https://github.com/org/repo/pull/1247#discussion_r12345",
        "author": "Bob Park",
        "date": "2025-05-11"
      }
    }
  ]
}
```

### Shared Engine: Extension ↔ MCP

The Python retrieval engine (`ast_intel.history`) is the single source of truth. Two consumption paths:

| Consumer | Transport | Symbol Resolution |
|----------|-----------|-------------------|
| VS Code extension | Subprocess: `ast-intel history <symbol> --json` | `DocumentSymbolProvider` → pass file + line range |
| MCP server | In-process: `QueryEngine.get_symbol_history()` | `GraphNode.id` lookup (AST-Intel graph) |

The extension shells out to the `ast-intel` CLI (new `history` subcommand) rather than embedding Python — this keeps the extension lightweight and avoids duplicating the retrieval engine in TypeScript.

#### Extension migration from Phase 1–2

Phase 1–2 ship the extension with TypeScript-native git parsers (`src/git/blame.ts`, `src/git/log.ts`). Phase 3 migrates the extension to the Python CLI as the source of truth, but does **not** delete the TypeScript code on day one:

- The TS git parsers are kept as a **fallback path** when `ast-intel` is not installed or is on an older version.
- A capability probe (`ast-intel --version`) decides which path to use at activation.
- Once the CLI bridge is GA and stable, the TS parsers are removed in a Phase 3.x cleanup release.

This avoids a hard break for existing extension users while the shared engine matures.

### Success Metrics

| Metric | Target | How Measured |
|--------|--------|-------------|
| MCP tool response latency (cached, single symbol) | < 1s | Internal timer |
| MCP tool response latency (cold, with provider enrichment) | < 5s | Internal timer |
| Citation completeness | 100% of signals have a citation | Schema validation |
| Agent task success rate (Copilot using history tools on 10 tasks) | > 70% successful tool use | Manual eval |
| Graph node → symbol history resolution accuracy | > 95% | Automated test against known repos |

### Risk Factors

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| AST-Intel `GraphNode.span` is sometimes missing/inaccurate | Medium | High | Fall back to name-based `git log -S` search; file issue for span accuracy upstream |
| Subprocess invocation adds latency for extension | Medium | Medium | Cache aggressively; consider sidecar process in future |
| MCP tool schema changes break agent prompts | Low | High | Version the tool schemas; keep backward-compatible |
| History engine adds memory overhead to MCP server | Medium | Medium | Lazy-load history module; limit in-memory cache size |

### ADR-004: Shared Python Engine with CLI Bridge for Extension

#### Context
Both the VS Code extension and MCP server need the same history retrieval logic. Options: (a) implement twice (TypeScript + Python), (b) shared Python engine with subprocess bridge, (c) shared Python engine with language server protocol.

#### Decision
**Shared Python engine** (`ast_intel.history`) consumed by the MCP server in-process and by the VS Code extension via `ast-intel history` CLI subprocess calls.

#### Consequences

**Positive:**
- Single implementation — no drift between extension and MCP
- Python is already AST-Intel's language; reuses existing infrastructure
- CLI subprocess is simple to implement and debug
- Extension stays lightweight (no Python runtime bundled)

**Negative:**
- Subprocess overhead (~200–500ms cold start per call)
- Requires `ast-intel` to be installed in the user's environment
- No streaming — must wait for full response

**Alternatives Considered:**
- **Dual implementation**: Rejected — maintenance burden, inevitable drift
- **LSP bridge**: Over-engineered for Phase 3; reconsider if latency becomes a real problem

**Status:** Accepted

### ADR-005: Extend QueryEngine Rather Than Separate Service

#### Context
Should history queries go through the existing `QueryEngine` or be a separate service/module with its own entry points?

#### Decision
**Extend `QueryEngine`** with `get_symbol_history`, `get_ownership`, and `get_decision_context` methods. The `HistoryBuilder` is injected as an optional dependency.

#### Consequences

**Positive:**
- Leverages existing symbol resolution (`_node_map`, `_label_index`, `_suffix_index`)
- Single entry point for all graph queries — agents don't need to know about separate services
- Natural composition: `QueryEngine` already has the graph context needed to resolve symbols

**Negative:**
- `QueryEngine` grows larger (mitigated by delegation to `HistoryBuilder`)
- History features are coupled to `QueryEngine` initialization (mitigated by optional `history_builder` param — `None` means history tools are unavailable)

**Status:** Accepted

---

## Phase 4: Rich Signals — RFCs, Incidents, Tribal Knowledge

### Goals

1. Index **RFC/design documents** and link them to the symbols they describe
2. Link **incident reports** (PagerDuty, Jira, etc.) to the code that was implicated
3. Capture **tribal knowledge** — developer annotations, team notes, and oral history — with an accept/reject feedback loop
4. Add **confidence scoring** to all signals (not just ownership) and **conflict detection** when sources disagree
5. Build a feedback loop so developers and agents can mark signals as correct, outdated, or wrong

### Dependencies on Prior Phases

- Phase 3: MCP tools, shared retrieval engine, `QueryEngine` integration, citation format
- Phase 2: Provider adapters (reused for issue/incident APIs)
- Phase 1: Git-native history (baseline signal)

### Components to Build

```
ast_intel/
├── history/
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── rfc_indexer.py          # Parse markdown/Notion/Confluence RFCs → symbol links
│   │   ├── incident_linker.py      # PagerDuty/Jira/Opsgenie → symbol links
│   │   ├── tribal_knowledge.py     # Manual annotations store
│   │   └── signal_aggregator.py    # Merge all signals, score, detect conflicts
│   ├── scoring/
│   │   ├── confidence.py           # Unified confidence model
│   │   └── conflict_detector.py    # Flag contradictory signals
│   ├── feedback/
│   │   ├── store.py                # Persist accept/reject/edit feedback
│   │   └── loop.py                 # Adjust confidence based on feedback
│   └── models.py                   # EXTENDED — RFCLink, IncidentLink, TribalNote, etc.
```

**Complexity**: **XL** — RFC indexing is L, incident linking is L, tribal knowledge is M, confidence/conflict scoring is L, feedback loop is M.

### Key Data Models

```python
@dataclass(frozen=True, slots=True)
class RFCLink:
    """A link between an RFC/design doc and a code symbol."""
    rfc_id: str
    rfc_title: str
    rfc_url: str
    section: str                    # Which section mentions the symbol
    excerpt: str                    # Relevant text excerpt
    symbols: tuple[SymbolRef, ...]  # Symbols this RFC discusses
    author: str
    created_at: datetime
    status: str                     # "draft" | "accepted" | "superseded"


@dataclass(frozen=True, slots=True)
class IncidentLink:
    """A link between an incident and a code symbol."""
    incident_id: str
    title: str
    url: str
    severity: str                   # "sev1" | "sev2" | "sev3" | "sev4"
    status: str                     # "resolved" | "investigating" | "monitoring"
    root_cause_symbols: tuple[SymbolRef, ...]
    fix_commit: str | None = None
    fix_pr: str | None = None
    occurred_at: datetime = field(default_factory=datetime.now)
    resolved_at: datetime | None = None
    postmortem_url: str = ""


@dataclass(frozen=True, slots=True)
class TribalNote:
    """Developer-annotated knowledge about a symbol."""
    id: str
    symbol: SymbolRef
    author: str
    text: str
    created_at: datetime
    tags: tuple[str, ...] = ()      # e.g. ("perf-sensitive", "legacy", "do-not-touch")
    upvotes: int = 0
    downvotes: int = 0


class SignalType(StrEnum):
    GIT_BLAME = "git_blame"
    GIT_LOG = "git_log"
    PR_DESCRIPTION = "pr_description"
    REVIEW_COMMENT = "review_comment"
    RFC = "rfc"
    INCIDENT = "incident"
    TRIBAL_KNOWLEDGE = "tribal_knowledge"


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    """A signal about a symbol with confidence scoring."""
    signal_type: SignalType
    text: str
    author: str
    date: datetime
    confidence: float               # 0.0–1.0
    citation_url: str = ""
    citation_detail: str = ""
    conflicts_with: tuple[str, ...] = ()  # IDs of conflicting signals


@dataclass(frozen=True, slots=True)
class ConflictReport:
    """Two or more signals that contradict each other."""
    symbol: SymbolRef
    signals: tuple[ScoredSignal, ...]
    conflict_type: str              # "ownership_dispute" | "intent_mismatch" | "stale_rfc"
    recommended_resolution: str


class FeedbackVerdict(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    OUTDATED = "outdated"


@dataclass(frozen=True, slots=True)
class FeedbackEntry:
    signal_id: str
    verdict: FeedbackVerdict
    author: str
    comment: str = ""
    replacement_text: str = ""      # If verdict == "edit"
    created_at: datetime = field(default_factory=datetime.now)
```

### Confidence Scoring Model

All signals — not just ownership — receive a confidence score:

| Signal Type | Base Confidence | Decay | Boost |
|---|---|---|---|
| `git_blame` (current HEAD) | 0.95 | None (always current) | — |
| `git_log` (commit message) | 0.70 | −0.05/year | +0.10 if conventional commit format |
| `pr_description` | 0.85 | −0.03/year | +0.10 if merged (not closed) |
| `review_comment` | 0.80 | −0.05/year | +0.15 if from code owner |
| `rfc` | 0.75 | −0.10/year if status ≠ "accepted" | +0.20 if status = "accepted" |
| `incident` | 0.90 | −0.02/year | +0.10 if postmortem exists |
| `tribal_knowledge` | 0.50 | −0.15/year | +0.05 per upvote (cap 0.30) |

Feedback adjustments:
- `accept` → +0.10 (capped at 1.0)
- `reject` → −0.30 (floor 0.0, signal hidden if < 0.1)
- `outdated` → −0.20

### Conflict Detection Rules

| Conflict Type | Trigger | Example |
|---|---|---|
| `ownership_dispute` | Two authors have ownership scores within 0.05 of each other | Alice=0.41, Bob=0.39 |
| `intent_mismatch` | RFC says "X should do Y" but recent PR description says "X now does Z" | RFC: "processPayment uses Stripe" vs PR: "Migrate processPayment to Adyen" |
| `stale_rfc` | RFC links to symbol but RFC status is "superseded" or last update > 2 years | — |
| `incident_recurrence` | Same symbol appears in 2+ incidents within 6 months | — |

Conflict detection is **heuristic-based** in Phase 4. Full semantic conflict detection (NLP-based) is deferred to a future phase.

### RFC Indexing Strategy

RFCs are discovered via configurable glob patterns (e.g., `docs/rfcs/**/*.md`, `design/**/*.md`). The indexer:

1. Parses each markdown file
2. Extracts symbol references via backtick-quoted names (`` `processPayment` ``)
3. Cross-references with `CodeGraph.nodes` by label matching
4. Stores `RFCLink` with excerpt context (±3 paragraphs around mention)

Notion/Confluence support is deferred — Phase 4 focuses on markdown files in the repo.

### New MCP Tools

```python
# get_symbol_context (extended) — now includes RFCs, incidents, tribal notes
Tool(name="get_decision_context", ...)  # Extended to include RFC and incident signals

# New tools
Tool(
    name="get_incident_history",
    description="Get incidents linked to a code symbol. Includes severity, resolution, and postmortem links.",
    inputSchema={...},
)

Tool(
    name="add_tribal_knowledge",
    description="Add a developer note about a code symbol. Other developers and agents can upvote/downvote.",
    inputSchema={...},
)

Tool(
    name="submit_feedback",
    description="Accept, reject, or mark a signal as outdated. Adjusts confidence scoring.",
    inputSchema={...},
)
```

### Feedback Loop Architecture

```
Agent/Developer ──→ submit_feedback(signal_id, verdict) ──→ FeedbackStore
                                                                │
                                                                ▼
                                                          Adjust confidence
                                                                │
                                                                ▼
                                                          Re-rank signals
                                                                │
                                                                ▼
                                                     Next query returns
                                                     updated scores
```

Feedback is stored as a JSON Lines file (`.ast-intel/feedback.jsonl`) scoped to the repository — one `FeedbackEntry` per line, append-only. This avoids external dependencies, makes appends atomic, and keeps merge conflicts line-scoped if the file is shared via git. Future: sync feedback to a team-shared backend.

### Success Metrics

| Metric | Target | How Measured |
|--------|--------|-------------|
| RFC → symbol linking accuracy | > 75% (markdown-only repos) | Manual audit on 3 repos with RFCs |
| Incident → symbol linking accuracy | > 60% (requires commit SHA in incident) | Manual audit |
| Conflict detection precision | > 70% (flagged conflicts are real) | Manual review |
| Feedback loop effect | Rejected signals drop from top-3 in next query | Automated test |
| Tribal knowledge adoption | > 10 notes added per repo per month (team of 10) | Telemetry |
| End-to-end `get_decision_context` with all signals | < 3s (cached) | Internal timer |

### Risk Factors

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| RFC indexing produces false-positive symbol links | High | Medium | Require backtick-quoted names; add confidence threshold; enable user feedback to correct |
| Incident data is behind auth-walled APIs (PagerDuty, Jira) | High | High | Phase 4.0: manual incident entry via `add_tribal_knowledge`; Phase 4.1: API adapters |
| Tribal knowledge becomes noisy / stale | Medium | Medium | Decay function + downvotes; auto-flag notes on symbols with 0 commits in 6+ months |
| Confidence model needs per-team tuning | Medium | Medium | Expose weights as config; provide "calibrate" command that suggests weights from feedback history |
| Conflict detection false positives annoy users | Medium | High | Default to "info" severity; let users dismiss; adjust threshold based on dismiss rate |

### ADR-006: Local-First Feedback Storage

#### Context
The feedback loop (accept/reject signals) needs persistence. Options: (a) external database, (b) git-tracked file, (c) local untracked file.

#### Decision
Store feedback in `.ast-intel/feedback.jsonl` (JSON Lines, append-only). The extension/CLI does **not** modify the user's `.gitignore` automatically; instead, the README and `ast-intel init` command recommend adding `.ast-intel/feedback.jsonl` to `.gitignore` if the team prefers local-only feedback. Teams that want to share feedback simply commit the file.

#### Consequences

**Positive:**
- Zero infrastructure — works offline, no database
- Users control sharing (commit or gitignore — explicit, not implicit)
- Simple implementation — append-only JSONL with line-scoped merge conflicts

**Negative:**
- No cross-machine sync by default
- Potential merge conflicts if shared via git
- No team-wide aggregation without external tooling

**Alternatives Considered:**
- **SQLite**: More structured, but adds dependency and complicates git sharing
- **External API**: Maximum collaboration, but violates "works offline" principle and adds infra

**Status:** Accepted

---

## Appendix A: Full Phase Timeline

```
Phase 1 ████████████████░░░░░░░░░░░░░░░░░░░░░░░░  (L)   Git-native history + ownership
Phase 2 ░░░░░░░░░░░░░░░░████████████████████░░░░░  (XL)  Provider adapters + enrichment
Phase 3 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░█████████  (XL)  MCP integration + shared engine
Phase 4 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░██  (XL)  Rich signals + feedback loop
        ^                                         ^
        Start                                   Mature
```

## Appendix B: Dependency Graph

```
Phase 1: Foundation
  └──→ Phase 2: Provider-Enriched History
        ├──→ Phase 3: MCP Integration
        │     └──→ Phase 4: Rich Signals
        └──────────→ Phase 4: Rich Signals (provider adapters reused for incident APIs)
```

## Appendix C: Configuration Schema

```jsonc
// .vscode/settings.json (extension settings)
{
  "codeHistoryIntel.ownership.weights": {
    "commitFrequency": 0.40,
    "recency": 0.35,
    "lineOwnership": 0.25
  },
  "codeHistoryIntel.ownership.recencyDecayLambda": 0.005,
  "codeHistoryIntel.provider": "auto",           // "auto" | "github" | "ado" | "bitbucket" | "codecommit"
  "codeHistoryIntel.maxCommitsPerSymbol": 50,
  "codeHistoryIntel.cache.maxEntries": 500,
  "codeHistoryIntel.codeLens.enabled": true,
  "codeHistoryIntel.rfcGlobs": ["docs/rfcs/**/*.md", "design/**/*.md"],
  "codeHistoryIntel.feedback.shared": false       // true = don't gitignore feedback file
}
```

## Appendix D: AST-Intel Integration Points

| AST-Intel Component | Integration | Phase |
|---|---|---|
| `ExtractorBase` → `FileAST` | Provides `Span` for symbol location | 3 |
| `GraphBuilder` → `GraphNode` | Provides `node.id`, `node.span`, `node.file` for resolution | 3 |
| `QueryEngine` | Extended with `get_symbol_history`, `get_ownership`, `get_decision_context` | 3 |
| `NodeKind` | Mapped to `SymbolKind` for history queries | 3 |
| `mcp_server.py` tool registry | New tools registered alongside existing graph tools | 3 |
| `CodeGraph.edges` | Future: `RELATION_DECIDED_BY`, `RELATION_INCIDENT_FOR` edge types | 4 |
| `core/cache.py` | Reuse caching patterns for history data | 3 |
