package methods

import "fmt"

// Server is the main server type.
type Server struct {
	Host string
	Port int
	name string
}

// Client connects to a server.
type Client struct {
	Endpoint string
	timeout  int
}

// Start launches the server (pointer receiver).
func (s *Server) Start() error {
	fmt.Printf("Starting %s on %s:%d\n", s.name, s.Host, s.Port)
	return nil
}

// Stop shuts down the server.
func (s *Server) Stop() {
	fmt.Println("Stopping server")
}

// Address returns the server address (value receiver).
func (s Server) Address() string {
	return fmt.Sprintf("%s:%d", s.Host, s.Port)
}

// isRunning is an unexported method.
func (s *Server) isRunning() bool {
	return s.Port > 0
}

// Connect connects the client to its endpoint.
func (c *Client) Connect() error {
	fmt.Printf("Connecting to %s\n", c.Endpoint)
	return nil
}

// Disconnect closes the client connection.
func (c *Client) Disconnect() {
	fmt.Println("Disconnecting")
}

// SetTimeout configures the client timeout (value receiver).
func (c Client) SetTimeout(ms int) Client {
	c.timeout = ms
	return c
}

// GenericService is a generic type with methods.
type GenericService[T any] struct {
	Items []T
}

// Add adds an item to the generic service.
func (g *GenericService[T]) Add(item T) {
	g.Items = append(g.Items, item)
}

// Count returns the number of items.
func (g *GenericService[T]) Count() int {
	return len(g.Items)
}
