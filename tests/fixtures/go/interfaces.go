package interfaces

import "io"

// StorageHelper defines the storage contract.
type StorageHelper interface {
	// Retrieve fetches data by key.
	Retrieve(key string) ([]byte, error)
	// Store saves data under the given key.
	Store(key string, data []byte) error
}

// Serializable is unexported.
type serializable interface {
	Serialize() ([]byte, error)
	Deserialize(data []byte) error
}

// Repository extends StorageHelper with generic support.
type Repository[T any] interface {
	StorageHelper
	FindByID(id string) (T, error)
	FindAll() ([]T, error)
}

// CombinedHelper combines multiple interfaces.
type CombinedHelper interface {
	StorageHelper
	serializable
	Close() error
}

// ReadWriteCloser extends standard library interfaces.
type ReadWriteCloser interface {
	io.Reader
	io.Writer
	Close() error
}

// Config has property-like methods (getters).
type Config interface {
	Name() string
	Port() int
	IsEnabled() bool
}

// EmptyInterface has no methods.
type EmptyInterface interface{}
