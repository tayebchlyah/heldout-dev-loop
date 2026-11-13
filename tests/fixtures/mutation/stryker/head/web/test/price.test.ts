import { expect, test } from "vitest";
import { check, discount } from "../src/core/price";

test("discount", () => {
  expect(discount(200)).toBe(10);
  expect(discount(50)).toBe(0);
});

test("check", () => {
  expect(check(0)).toBe(0);
  expect(check(5)).toBe(5);
  expect(() => check(-1)).toThrow();
});
