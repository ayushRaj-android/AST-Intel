"""AST node dataclasses — the canonical schema for all extracted code elements.

All language extractors produce instances of these dataclasses. They are
pure data containers with no parsing logic. The normalized schema ensures
cross-language queries work uniformly.

Language Concept Mapping:
    ========================  ===============
    Language Concept          Normalized To
    ========================  ===============
    Rust trait / Go interface → TraitNode
    Rust struct / Python class → StructNode
    Rust impl / Go receiver    → ImplBlockNode
    Rust enum / TS enum        → EnumNode
    ========================  ===============
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__: list[str] = [
    "SCORE_AMBIGUOUS",
    "SCORE_EXTRACTED",
    "SCORE_INFERRED",
    "SCORE_INFERRED_CROSS_FILE",
    "CallEdge",
    "Confidence",
    "ConstantNode",
    "EnumNode",
    "EnumVariantKind",
    "EnumVariantNode",
    "FieldNode",
    "FileAST",
    "FunctionNode",
    "HttpCallNode",
    "ImplBlockNode",
    "MacroNode",
    "MethodNode",
    "ModuleNode",
    "ParamNode",
    "PayloadField",
    "RationaleNode",
    "RouteNode",
    "SdkCallNode",
    "Span",
    "StructNode",
    "TraitItemKind",
    "TraitItemNode",
    "TraitNode",
    "TypeAliasNode",
    "Visibility",
]


# ---------------------------------------------------------------------------
# region:    --- Enumerations
# ---------------------------------------------------------------------------


class Visibility(StrEnum):
    """Access visibility of a symbol.

    Maps to ``pub`` / ``private`` across all languages:
    - Rust: ``pub`` vs no modifier
    - Python: leading underscore convention
    - Go: uppercase first letter = exported
    - C#: ``public`` / ``private`` / ``internal``
    - TS/JS: ``export`` keyword
    """

    PUBLIC = "pub"
    PRIVATE = "private"
    CRATE = "crate"       # Rust pub(crate)
    PROTECTED = "protected"  # C++, C#, Java


class EnumVariantKind(StrEnum):
    """Kind of an enum variant.

    - ``unit``: No associated data (e.g., Rust ``Variant``, C# ``Value``)
    - ``tuple``: Positional data (e.g., Rust ``Variant(i32, String)``)
    - ``struct``: Named fields (e.g., Rust ``Variant { x: i32, y: i32 }``)
    """

    UNIT = "unit"
    TUPLE = "tuple"
    STRUCT = "struct"


class TraitItemKind(StrEnum):
    """Kind of item inside a trait / interface definition."""

    REQUIRED_METHOD = "required_method"
    DEFAULT_METHOD = "default_method"
    ASSOCIATED_TYPE = "associated_type"
    CONSTANT = "constant"


class Confidence(StrEnum):
    """Confidence level for an extracted data point.

    Indicates how the data was obtained — directly from the source AST
    (``EXTRACTED``), via a reasonable heuristic (``INFERRED``), or when
    the result is uncertain due to ambiguity (``AMBIGUOUS``).

    Consumers can filter or weight results by confidence:

    - **EXTRACTED** — guaranteed correct; parsed directly from syntax.
    - **INFERRED** — usually correct; derived from naming conventions
      or cross-file resolution heuristics.
    - **AMBIGUOUS** — uncertain; multiple candidates or weak signal.
    """

    EXTRACTED = "extracted"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"


# Default confidence scores (range: 0.0 – 1.0).
#
# SCORE_EXTRACTED (1.0): Certainty — data parsed directly from AST syntax.
# SCORE_INFERRED (0.8): Heuristic-based detection in extractors
#   (e.g., UPPER_CASE constants, CamelCase type aliases, base-class impls).
# SCORE_INFERRED_CROSS_FILE (0.85): Cross-file name resolution in the
#   indexer — slightly higher than extractor heuristics because the function
#   name matched exactly one definition across the workspace.
# SCORE_AMBIGUOUS (0.5): Multiple candidates, outcome uncertain.
SCORE_EXTRACTED: float = 1.0
SCORE_INFERRED: float = 0.8
SCORE_INFERRED_CROSS_FILE: float = 0.85
SCORE_AMBIGUOUS: float = 0.5


# endregion: --- Enumerations


# ---------------------------------------------------------------------------
# region:    --- Source Span
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Span:
    """Source location span for an AST node.

    All positions are 1-based (matching editor conventions) unless
    otherwise noted. Tree-sitter provides 0-based ``start_point`` /
    ``end_point`` tuples — extractors convert to 1-based when
    constructing this dataclass.

    Attributes:
        start_line: First line of the node (1-based).
        start_col: First column of the node (1-based).
        end_line: Last line of the node (1-based).
        end_col: Last column of the node (1-based, exclusive).
    """

    start_line: int
    start_col: int
    end_line: int
    end_col: int


# endregion: --- Source Span


# ---------------------------------------------------------------------------
# region:    --- Struct & Field Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldNode:
    """A single field in a struct / class.

    Attributes:
        name: Field identifier.
        type: Type annotation as source text (e.g., ``Arc<S>``, ``string``).
        visibility: Access visibility.
    """

    name: str
    type: str
    visibility: Visibility = Visibility.PRIVATE


@dataclass(frozen=True, slots=True)
class StructNode:
    """A struct, class, record, or equivalent named data type.

    Attributes:
        name: Type name.
        visibility: Access visibility.
        generics: Generic parameters as raw text (e.g., ``<S, T>``), empty if none.
        fields: Ordered list of fields.
        attributes: Decorators / derive macros / annotations as raw strings.
        doc: Doc comment text, if present.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    generics: str = ""
    fields: tuple[FieldNode, ...] = ()
    attributes: tuple[str, ...] = ()
    doc: str = ""
    span: Span | None = None


# endregion: --- Struct & Field Nodes


# ---------------------------------------------------------------------------
# region:    --- Enum Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnumVariantNode:
    """A single variant inside an enum.

    Attributes:
        name: Variant identifier.
        kind: Whether the variant is unit, tuple, or struct.
        fields: Fields if the variant is ``tuple`` or ``struct`` kind.
    """

    name: str
    kind: EnumVariantKind = EnumVariantKind.UNIT
    fields: tuple[FieldNode, ...] = ()


@dataclass(frozen=True, slots=True)
class EnumNode:
    """An enum type definition.

    Attributes:
        name: Enum name.
        visibility: Access visibility.
        generics: Generic parameters as raw text.
        variants: Ordered list of variants.
        attributes: Decorators / derive macros / annotations.
        doc: Doc comment text.
        span: Source location span.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    generics: str = ""
    variants: tuple[EnumVariantNode, ...] = ()
    attributes: tuple[str, ...] = ()
    doc: str = ""
    span: Span | None = None


# endregion: --- Enum Nodes


# ---------------------------------------------------------------------------
# region:    --- Function & Method Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParamNode:
    """A function / method parameter.

    Attributes:
        name: Parameter name.
        type: Type annotation as source text.
    """

    name: str
    type: str = ""


@dataclass(frozen=True, slots=True)
class FunctionNode:
    """A free function (not attached to a type / class).

    Attributes:
        name: Function name.
        visibility: Access visibility.
        is_async: Whether the function is async.
        is_unsafe: Whether the function is marked unsafe (Rust-specific).
        generics: Generic parameters as raw text.
        params: Ordered list of parameters.
        return_type: Return type as source text, empty if void / unit.
        where_clause: Where clause as raw text (Rust-specific).
        attributes: Decorators / annotations.
        doc: Doc comment text.
        span: Source location span.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    is_async: bool = False
    is_unsafe: bool = False
    generics: str = ""
    params: tuple[ParamNode, ...] = ()
    return_type: str = ""
    where_clause: str = ""
    attributes: tuple[str, ...] = ()
    doc: str = ""
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class MethodNode:
    """A method defined in or associated with a type.

    Used in two contexts:
    1. Inside ``ImplBlockNode.methods`` — methods within an impl/class block.
    2. In ``FileAST.self_methods`` — flat list of every method defined in a file,
       with ``context`` indicating where it was defined.

    Attributes:
        name: Method name.
        visibility: Access visibility.
        is_async: Whether the method is async.
        is_unsafe: Whether the method is marked unsafe.
        is_static: Whether the method is static (no self/this receiver).
        params: Parameters (excluding self/this).
        return_type: Return type as source text.
        context: Where this method is defined. For ``self_methods``, this is
            a string like ``"impl:TraitName for TypeName"`` or ``"free"``.
        attributes: Decorators / annotations.
        doc: Doc comment text.
        span: Source location span.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    is_async: bool = False
    is_unsafe: bool = False
    is_static: bool = False
    params: tuple[ParamNode, ...] = ()
    return_type: str = ""
    context: str = ""
    attributes: tuple[str, ...] = ()
    doc: str = ""
    span: Span | None = None


# endregion: --- Function & Method Nodes


# ---------------------------------------------------------------------------
# region:    --- Trait Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraitItemNode:
    """An item inside a trait / interface definition.

    Attributes:
        kind: Whether this is a required method, default method, associated type, etc.
        name: Item name.
        is_async: For method items, whether async.
        params: For method items, parameter list.
        return_type: For method items, return type.
        doc: Doc comment text.
        span: Source location span.
    """

    kind: TraitItemKind
    name: str
    is_async: bool = False
    params: tuple[ParamNode, ...] = ()
    return_type: str = ""
    doc: str = ""
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class TraitNode:
    """A trait, interface, protocol, or abstract base class.

    Attributes:
        name: Trait / interface name.
        visibility: Access visibility.
        generics: Generic parameters as raw text.
        super_traits: Parent traits / extended interfaces.
        items: Methods and associated types inside the trait.
        attributes: Decorators / annotations.
        doc: Doc comment text.
        span: Source location span.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    generics: str = ""
    super_traits: tuple[str, ...] = ()
    items: tuple[TraitItemNode, ...] = ()
    attributes: tuple[str, ...] = ()
    doc: str = ""
    span: Span | None = None


# endregion: --- Trait Nodes


# ---------------------------------------------------------------------------
# region:    --- Impl Block Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImplBlockNode:
    """An implementation block (Rust ``impl``, class body, receiver methods).

    Attributes:
        self_type: The type being implemented (e.g., ``AsyncComponent<S, T>``).
        trait_type: The trait being implemented, or empty for inherent impls.
        generics: Generic parameters on the impl block.
        methods: Methods defined in this implementation.
        span: Source location span.
        confidence: ``EXTRACTED`` for explicit syntax (Rust/TS/Go/C#),
            ``INFERRED`` for Python base-class detection heuristic.
        confidence_score: Numeric confidence in ``[0.0, 1.0]``.
    """

    self_type: str
    trait_type: str = ""
    generics: str = ""
    methods: tuple[MethodNode, ...] = ()
    span: Span | None = None
    confidence: Confidence = Confidence.EXTRACTED
    confidence_score: float = 1.0


# endregion: --- Impl Block Nodes


# ---------------------------------------------------------------------------
# region:    --- Auxiliary Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeAliasNode:
    """A type alias (``type Alias = Original``, ``using``, ``typedef``).

    Attributes:
        name: Alias name.
        aliased_to: The target type as source text.
        visibility: Access visibility.
        span: Source location span.
        confidence: ``EXTRACTED`` for explicit ``TypeAlias`` annotation,
            ``INFERRED`` for CamelCase naming heuristic.
        confidence_score: Numeric confidence in ``[0.0, 1.0]``.
    """

    name: str
    aliased_to: str = ""
    visibility: Visibility = Visibility.PRIVATE
    span: Span | None = None
    confidence: Confidence = Confidence.EXTRACTED
    confidence_score: float = 1.0


@dataclass(frozen=True, slots=True)
class ConstantNode:
    """A constant or static variable.

    Attributes:
        name: Constant name.
        visibility: Access visibility.
        raw: Full declaration as source text (for display).
        span: Source location span.
        confidence: ``EXTRACTED`` for typed annotations or explicit
            ``const``/``static`` syntax, ``INFERRED`` for UPPER_CASE
            naming heuristic.
        confidence_score: Numeric confidence in ``[0.0, 1.0]``.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    raw: str = ""
    span: Span | None = None
    confidence: Confidence = Confidence.EXTRACTED
    confidence_score: float = 1.0


@dataclass(frozen=True, slots=True)
class ModuleNode:
    """A module / namespace declaration.

    Attributes:
        name: Module name.
        visibility: Access visibility.
        inline: True if the module body is inline (``mod x { ... }``),
            False if file-backed (``mod x;``).
        span: Source location span.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    inline: bool = False
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class MacroNode:
    """A macro definition (Rust ``macro_rules!``, C ``#define``).

    Macro *expansions* are not captured — only the definition.

    Attributes:
        name: Macro name.
        visibility: Access visibility.
        doc: Doc comment text.
        span: Source location span.
    """

    name: str
    visibility: Visibility = Visibility.PRIVATE
    doc: str = ""
    span: Span | None = None


@dataclass(frozen=True, slots=True)
class RationaleNode:
    """A rationale comment extracted from source code.

    Captures design decisions, known issues, and important notes
    from specially-prefixed comments (``NOTE:``, ``HACK:``, ``TODO:``,
    ``FIXME:``, ``WHY:``, ``IMPORTANT:``, ``SAFETY:``, ``RATIONALE:``,
    ``PERF:``).

    Attributes:
        kind: The prefix category (e.g., ``"NOTE"``, ``"HACK"``).
        text: Comment body text without the prefix.
        span: Source location span.
        parent: Name of the enclosing function / class / module, or
            ``"<file>"`` for file-level comments.
    """

    kind: str
    text: str
    span: Span
    parent: str


# endregion: --- Auxiliary Nodes


# ---------------------------------------------------------------------------
# region:    --- HTTP Route Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RouteNode:
    """An HTTP endpoint definition extracted from source code.

    Captures route registrations from web frameworks (Axum, Actix,
    FastAPI, Flask, Express, Spring) as structured data.

    Attributes:
        path: URL path pattern (e.g., ``"/api/users/{id}"``).
        method: HTTP method (``"GET"``, ``"POST"``, etc.) or ``"*"``.
        handler: Name of the handler function/method.
        framework: Framework that defines this route.
        span: Source location of the route definition.
    """

    path: str
    method: str
    handler: str
    framework: str
    span: Span | None = None


# endregion: --- HTTP Route Nodes


# ---------------------------------------------------------------------------
# region:    --- HTTP Client Call Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PayloadField:
    """A single piece of data sent as part of an outgoing HTTP call.

    Captures *what* is sent — a body field, header, or query parameter —
    at the call site, without evaluating runtime values.

    Attributes:
        location: Where the data travels — ``"body"``, ``"header"``, or
            ``"query"``.
        name: The field/key name (e.g. ``"user_id"``, ``"Authorization"``).
            Empty when the whole payload is a single variable/expression
            with no statically-known key.
        value: Literal value, variable name, or truncated expression text.
            Replaced with ``"<redacted>"`` when detected as a secret.
        value_kind: How ``value`` was obtained — ``"literal"`` (a constant
            in source), ``"variable"`` (a bare identifier, resolved later
            by intra-function data-flow), or ``"expression"`` (a call,
            attribute access, or other computed value).
        redacted: ``True`` when the value was withheld because the field
            name or value matched a secret pattern.
        source_kind: Where the value originates, resolved by intra-function
            data-flow (L2): ``"literal"``, ``"from-input"``, ``"from-db"``,
            ``"from-env"``, ``"computed"``, or ``"unknown"``.
    """

    location: str
    name: str
    value: str
    value_kind: str
    redacted: bool = False
    source_kind: str = "unknown"


@dataclass(frozen=True, slots=True)
class HttpCallNode:
    """An outgoing HTTP client call extracted from source code.

    Captures calls to HTTP client libraries (``reqwest``, ``requests``,
    ``fetch``, ``axios``, etc.) as structured data.

    Attributes:
        url: URL string or pattern.  Interpolated segments are
            replaced with ``{param}``; fully dynamic URLs become
            ``"<dynamic>"``.
        method: HTTP method (``"GET"``, ``"POST"``, etc.) or
            ``"UNKNOWN"`` when the method cannot be determined.
        library: Client library name (``"reqwest"``, ``"requests"``,
            ``"fetch"``, ``"axios"``, etc.).
        caller: Name of the enclosing function/method, or
            ``"<module>"`` for top-level calls.
        span: Source location of the call expression.
        payload: Body fields, headers, and query params sent with the
            call.  Empty when no outbound data payload was detected.
        payload_confidence: Capture quality of ``payload`` (meaningful
            only when ``payload`` is non-empty): ``EXTRACTED`` for
            literal keys/values, ``INFERRED`` when values come from
            variables, ``AMBIGUOUS`` when partially dynamic.
    """

    url: str
    method: str
    library: str
    caller: str
    span: Span | None = None
    payload: tuple[PayloadField, ...] = ()
    payload_confidence: Confidence = Confidence.EXTRACTED


# endregion: --- HTTP Client Call Nodes


# ---------------------------------------------------------------------------
# region:    --- SDK Egress Call Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SdkCallNode:
    """An outbound call to a known third-party SDK (Stripe, OpenAI, ...).

    Captures SDK method invocations that send data to an external vendor —
    the SaaS analogue of :class:`HttpCallNode` for calls that do not go
    through a raw HTTP client.

    Attributes:
        vendor: Vendor name (e.g. ``"Stripe"``, ``"OpenAI"``).
        category: Service category (``"payments"``, ``"ai"``, ...).
        sdk: The imported package/module the call resolves to
            (e.g. ``"stripe"``, ``"sentry_sdk"``).
        method: Dotted call path at the call site
            (e.g. ``"stripe.Charge.create"``).
        caller: Name of the enclosing function/method, or ``"<module>"``.
        span: Source location of the call expression.
        payload: Arguments sent with the call, captured as body fields.
        payload_confidence: Capture quality of ``payload`` (meaningful only
            when ``payload`` is non-empty).
    """

    vendor: str
    category: str
    sdk: str
    method: str
    caller: str
    span: Span | None = None
    payload: tuple[PayloadField, ...] = ()
    payload_confidence: Confidence = Confidence.EXTRACTED


# endregion: --- SDK Egress Call Nodes


# ---------------------------------------------------------------------------
# region:    --- Cloud Resource Nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloudResourceNode:
    """A cloud infrastructure resource usage detected from application code.

    Captures instantiation of cloud SDK clients (``CosmosClient``,
    ``boto3.client('sqs')``, ``redis.Redis``, etc.) as structured data.

    Attributes:
        provider: Cloud provider (``"azure"``, ``"aws"``, ``"gcp"``, ``"generic"``).
        service: Specific service name (e.g. ``"Cosmos DB"``, ``"SQS"``).
        category: Resource category (``"database"``, ``"cache"``, ``"queue"``,
            ``"topic"``, ``"stream"``, ``"storage"``, ``"secret"``, ``"other"``).
        client: SDK client class/function name (e.g. ``"CosmosClient"``).
        caller: Name of the enclosing function/method, or ``"<module>"``
            for top-level usage.
        name: Best-effort resource name (literal arg, config key, or empty).
        span: Source location of the client instantiation/call.
    """

    provider: str
    service: str
    category: str
    client: str
    caller: str
    name: str = ""
    span: Span | None = None


# endregion: --- Cloud Resource Nodes


# ---------------------------------------------------------------------------
# region:    --- Call Graph Edges
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallEdge:
    """A call-site from one function/method to another.

    The *caller* is the enclosing function where the call appears.
    The *callee* is the name of the called function or method.

    ``resolved_target`` is populated when the callee can be matched
    to a function defined in the same file (intra-file) or across
    files (cross-file, via the indexer).  When empty, the call is
    unresolved.

    Attributes:
        caller: Qualified name of the calling function.
        callee: Name of the called function/method as it appears at
            the call site.
        call_site: Source location of the call expression.
        resolved_target: Qualified name of the resolved target, or
            empty when unresolved.
        is_method_call: ``True`` for ``obj.method()`` style calls.
        confidence: How the edge was determined — ``EXTRACTED`` for
            intra-file resolution, ``INFERRED`` for cross-file,
            ``AMBIGUOUS`` when multiple candidates exist.
        confidence_score: Numeric confidence in the range ``[0.0, 1.0]``.
            See ``SCORE_*`` module constants for standard values.
    """

    caller: str
    callee: str
    call_site: Span
    resolved_target: str = ""
    is_method_call: bool = False
    confidence: Confidence = Confidence.EXTRACTED
    confidence_score: float = 1.0


# endregion: --- Call Graph Edges


# ---------------------------------------------------------------------------
# region:    --- File-Level AST
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FileAST:
    """Complete AST extraction for a single source file.

    This is the primary data unit produced by every language extractor.
    It contains all symbols, relationships, and package method references
    found in one file.

    Attributes:
        file: Workspace-relative file path (forward slashes).
        module_path: Fully qualified module path (e.g., ``crate::module::file``).
        is_test: Whether this file is a test file.
        uses: Raw import / use statements as strings.
        modules: Sub-module declarations.
        structs: Struct / class / record definitions.
        enums: Enum definitions.
        traits: Trait / interface / ABC definitions.
        functions: Free (non-method) function definitions.
        impl_blocks: Implementation blocks (impl, class body, receiver methods).
        type_aliases: Type alias definitions.
        constants: Constant / static definitions.
        macros: Macro definitions.
        self_methods: Flat list of every method defined in this file, with context.
        imported_package_methods: Scoped method calls grouped by resolved type.
            Keys are qualified type paths (e.g., ``bytes::BytesMut``).
            Values are lists of method names called on that type.
        errors: Parse errors encountered in this file.
    """

    file: str = ""
    module_path: str = ""
    is_test: bool = False
    uses: list[str] = field(default_factory=list)
    modules: list[ModuleNode] = field(default_factory=list)
    structs: list[StructNode] = field(default_factory=list)
    enums: list[EnumNode] = field(default_factory=list)
    traits: list[TraitNode] = field(default_factory=list)
    functions: list[FunctionNode] = field(default_factory=list)
    impl_blocks: list[ImplBlockNode] = field(default_factory=list)
    type_aliases: list[TypeAliasNode] = field(default_factory=list)
    constants: list[ConstantNode] = field(default_factory=list)
    macros: list[MacroNode] = field(default_factory=list)
    self_methods: list[MethodNode] = field(default_factory=list)
    imported_package_methods: dict[str, list[str]] = field(default_factory=dict)
    call_edges: list[CallEdge] = field(default_factory=list)
    rationale_comments: list[RationaleNode] = field(default_factory=list)
    routes: list[RouteNode] = field(default_factory=list)
    http_calls: list[HttpCallNode] = field(default_factory=list)
    cloud_resources: list[CloudResourceNode] = field(default_factory=list)
    sdk_calls: list[SdkCallNode] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# endregion: --- File-Level AST
