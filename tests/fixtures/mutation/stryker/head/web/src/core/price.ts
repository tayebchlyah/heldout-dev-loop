export function discount(total: number): number {
  if (total > 100) {
    return 10;
  }
  return 0;
}

export function check(qty: number): number {
  if (qty < 0) {
    throw new Error("negative quantity");
  }
  return qty;
}

export function unused(x: number): number {
  return x * 2;
}
