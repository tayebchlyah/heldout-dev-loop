import { expect, test } from "vitest";
import { discount } from "../src/core/price";

test("discount", () => {
  expect(discount(5)).toBe(1);
  expect(discount(0)).toBe(0);
});
