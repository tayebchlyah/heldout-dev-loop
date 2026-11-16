export function discount(total: number): number {
  if (total > 100) {
    return 10;
  }
  return 0;
}
