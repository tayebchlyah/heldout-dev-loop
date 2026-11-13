package core

import "testing"

func TestDiscount(t *testing.T) {
	if Discount(50) != 0 {
		t.Fatal("50")
	}
}
