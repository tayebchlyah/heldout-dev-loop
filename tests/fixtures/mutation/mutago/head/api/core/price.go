package core

import (
	"errors"
	"fmt"
)

var ErrNegative = errors.New("negative")

func Discount(total int) int {
	if total > 100 {
		return 10
	}
	return 0
}

type Cart struct{ Qty int }

func (c *Cart) Check() (int, error) {
	if c.Qty < 0 {
		return 0, fmt.Errorf("quantity %d: %w", c.Qty, ErrNegative)
	}
	return c.Qty, nil
}

func Unused(x int) int {
	return x * 2
}
