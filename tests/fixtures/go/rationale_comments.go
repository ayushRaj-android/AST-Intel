package rationale

// NOTE: This package handles message routing
import "sync"

// TODO: Add support for priority queues
const MaxQueueSize = 1000

// HACK: Using a global mutex because the Router interface
// HACK: doesn't support per-route locking yet
var globalMutex sync.Mutex

type Router struct {
	routes map[string]Handler
}

type Handler func(msg []byte) error

// WHY: We copy the slice instead of using a reference because
// WHY: the caller may modify the original after dispatching
func (r *Router) Dispatch(route string, msg []byte) error {
	globalMutex.Lock()
	defer globalMutex.Unlock()
	handler, ok := r.routes[route]
	if !ok {
		return nil
	}
	copied := make([]byte, len(msg))
	copy(copied, msg)
	return handler(copied)
}

// FIXME: This function leaks goroutines on timeout
func processAsync(msg []byte) {
	go func() {
		_ = msg
	}()
}

// IMPORTANT: Changing this constant requires a rolling restart
const HeartbeatInterval = 30

// PERF: Pre-sizing the map avoids rehashing for typical workloads
func NewRouter(expectedRoutes int) *Router {
	return &Router{
		routes: make(map[string]Handler, expectedRoutes),
	}
}

// SAFETY: Caller must hold globalMutex before calling
func unsafeAddRoute(r *Router, name string, h Handler) {
	r.routes[name] = h
}

// RATIONALE: We use a struct wrapper instead of a raw map
// RATIONALE: to enforce invariants on route registration
type RouteRegistry struct {
	inner *Router
}
