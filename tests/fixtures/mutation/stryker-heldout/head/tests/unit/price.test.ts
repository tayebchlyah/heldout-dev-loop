import { expect, test } from "vitest";
import { discount } from "../../src/core/price";

test("discount", () => {
  expect(discount(200)).toBe(10);
  expect(discount(50)).toBe(0);
});
