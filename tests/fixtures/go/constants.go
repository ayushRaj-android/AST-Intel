package constants

// Exported standalone constant.
const MaxRetries = 3

// Unexported standalone constant.
const defaultTimeout = 30

// Typed constant.
const AppName string = "safeguard"

// Direction is a string type for iota-like constants.
type Direction int

// Direction enum constants.
const (
	North Direction = iota
	South
	East
	West
)

// Status constants with explicit values.
const (
	StatusOK    = 200
	StatusError = 500
	StatusNotFound = 404
)

// Exported variable.
var GlobalConfig = "production"

// unexported variable.
var internalState = 0

// Typed exported variable.
var MaxConnections int = 100

// Block variable declaration.
var (
	DefaultHost = "localhost"
	DefaultPort = 8080
)
