// Budget allocation for the Restocking view

const toCents = amount => Math.round(amount * 100)

// Greedy fill: walk candidates in the order given (the API sorts them by
// urgency) and give each its full recommended quantity while the budget lasts.
// When an item no longer fits in full, it gets as many units as the remaining
// budget buys, and the walk continues instead of stopping, so cheaper items
// further down can still use what is left.
//
// Works in integer cents: float division such as 0.3 / 0.1 = 2.999... would
// otherwise floor to one unit fewer than the budget actually affords.
export function allocateBudget(candidates, budget, excludedSkus = new Set()) {
  const budgetCents = toCents(budget)
  let remainingCents = budgetCents
  const lines = []

  for (const candidate of candidates) {
    if (excludedSkus.has(candidate.sku)) continue

    const unitCents = toCents(candidate.unit_cost)
    if (unitCents <= 0) continue

    const affordable = Math.floor(remainingCents / unitCents)
    const quantity = Math.min(candidate.recommended_quantity, affordable)
    if (quantity <= 0) continue

    const lineCents = quantity * unitCents
    remainingCents -= lineCents
    lines.push({ sku: candidate.sku, quantity, lineTotal: lineCents / 100 })
  }

  return {
    lines,
    total: (budgetCents - remainingCents) / 100,
    remaining: remainingCents / 100
  }
}
