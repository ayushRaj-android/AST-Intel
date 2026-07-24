package imports

import (
	"context"
	"fmt"
	"net/http"
	"os"

	alias "github.com/pkg/errors"
)

// UseImports exercises various imported package calls.
func UseImports() {
	fmt.Println("hello")
	fmt.Sprintf("formatted %s", "value")
	http.ListenAndServe(":8080", nil)
	http.NewRequest("GET", "/", nil)
	os.Getenv("HOME")
	alias.New("wrapped error")

	ctx := context.Background()
	_ = ctx
}
