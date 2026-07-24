package typedefs

// HandlerFunc is a type alias.
type HandlerFunc = func(string) error

// Middleware is a type definition (not alias).
type Middleware func(HandlerFunc) HandlerFunc

// StringMap is a named map type.
type StringMap map[string]string

// IDList is a named slice type.
type IDList []int64

// NodeID is a distinct type based on string.
type NodeID string

// ExportedAlias is an exported type alias.
type ExportedAlias = int

// unexportedDef is an unexported type definition.
type unexportedDef string
