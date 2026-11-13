package core

import "testing"

func TestDiscount(t *testing.T) {
	if Discount(200) != 10 || Discount(50) != 0 {
		t.Fatal("discount")
	}
}

func TestCheck(t *testing.T) {
	if _, err := (&Cart{Qty: -1}).Check(); err == nil {
		t.Fatal("no error")
	}
	if n, err := (&Cart{Qty: 2}).Check(); n != 2 || err != nil {
		t.Fatal("check")
	}
}
