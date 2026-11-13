import { discount } from "./core/price";

export function quote(total: number): number {
  return total - discount(total) * 2;
}
