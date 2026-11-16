import { expect, test } from "vitest";
import { discount } from "../../src/core/price";

test("no discount at the boundary", () => {
  expect(discount(100)).toBe(0);
});
