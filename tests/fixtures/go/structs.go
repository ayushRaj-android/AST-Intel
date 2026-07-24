package models

import "time"

// StorageConfig holds the configuration for a storage backend.
type StorageConfig struct {
	// BasePath is the root directory for file storage.
	BasePath string
	// MaxRetries controls how many times to retry on failure.
	MaxRetries int
	timeout    time.Duration
	labels     []string
}

// unexportedStruct is internal-only.
type unexportedStruct struct {
	id   int
	name string
}

// GenericContainer holds items of any comparable type.
type GenericContainer[T comparable, U any] struct {
	Items []T
	Meta  U
	count int
}

// EmbeddedStruct demonstrates struct embedding.
type EmbeddedStruct struct {
	StorageConfig
	Extra string
}

// QualifiedFields demonstrates qualified type fields.
type QualifiedFields struct {
	Deadline time.Time
	Elapsed  time.Duration
}

// PointerFields demonstrates pointer type fields.
type PointerFields struct {
	Config *StorageConfig
	Data   *[]byte
}

// MapFields demonstrates map type fields.
type MapFields struct {
	Entries map[string]int
	Nested  map[string][]byte
}
