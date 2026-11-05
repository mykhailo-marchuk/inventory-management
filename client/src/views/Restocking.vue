<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <!-- Order confirmation -->
      <div v-if="confirmation" class="confirmation-banner" role="status">
        <span>{{ t('restocking.orderPlaced', { orderNumber: confirmation.order_number, days: confirmation.lead_time_days }) }}</span>
        <router-link to="/orders" class="confirmation-link">{{ t('restocking.viewInOrders') }}</router-link>
      </div>

      <!-- Budget slider -->
      <div class="card budget-card">
        <div class="budget-top">
          <div>
            <div class="budget-label">{{ t('restocking.budget') }}</div>
            <div class="budget-value">{{ formatCurrency(budget) }}</div>
          </div>
          <div class="budget-full-cost">
            {{ t('restocking.fullRestockCost', { amount: formatCurrency(maxBudget) }) }}
          </div>
        </div>
        <input
          v-model.number="sliderValue"
          type="range"
          class="budget-slider"
          min="0"
          :max="sliderMax"
          :step="sliderStep"
          :disabled="candidates.length === 0"
          :aria-label="t('restocking.budget')"
          :aria-valuetext="formatCurrency(budget)"
        />
        <p class="budget-hint">{{ t('restocking.budgetHint') }}</p>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ formatCurrency(budget) }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.allocated') }}</div>
          <div class="stat-value">{{ formatCurrency(allocation.total) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ formatCurrency(allocation.remaining) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemsSelected') }}</div>
          <div class="stat-value">{{ allocation.lines.length }}</div>
        </div>
      </div>

      <!-- Recommendations -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ candidates.length }})</h3>
          <button
            type="button"
            class="place-order-button"
            :disabled="allocation.lines.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="submitError" class="error submit-error" role="alert">{{ submitError }}</div>

        <div v-if="candidates.length === 0" class="empty-state">
          {{ t('restocking.noRecommendations') }}
        </div>
        <div v-else class="table-container">
          <table class="restocking-table">
            <thead>
              <tr>
                <th class="col-include">{{ t('restocking.table.include') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.category') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.onHand') }}</th>
                <th class="num">{{ t('restocking.table.onOrder') }}</th>
                <th class="num">{{ t('restocking.table.forecast') }}</th>
                <th class="num">{{ t('restocking.table.recommended') }}</th>
                <th class="num">{{ t('restocking.table.allocated') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineCost') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in candidates"
                :key="item.sku"
                :class="{ muted: !allocatedLines[item.sku] }"
              >
                <td class="col-include">
                  <input
                    type="checkbox"
                    class="include-checkbox"
                    :checked="!excludedSkus.has(item.sku)"
                    :aria-label="`${t('restocking.table.include')}: ${translateProductName(item.name)}`"
                    @change="toggleSku(item.sku)"
                  />
                </td>
                <td>
                  <div class="item-name">{{ translateProductName(item.name) }}</div>
                  <div class="item-sku">{{ item.sku }}</div>
                </td>
                <td>{{ translateCategory(item.category) }}</td>
                <td>
                  <span :class="['badge', item.trend]">{{ translateTrend(item.trend) }}</span>
                </td>
                <td class="num">{{ item.quantity_on_hand.toLocaleString() }}</td>
                <td class="num">{{ item.quantity_on_order.toLocaleString() }}</td>
                <td class="num">{{ item.forecasted_demand.toLocaleString() }}</td>
                <td class="num">{{ item.recommended_quantity.toLocaleString() }}</td>
                <td class="num">
                  <strong v-if="allocatedLines[item.sku]">{{ allocatedLines[item.sku].quantity.toLocaleString() }}</strong>
                  <span v-else-if="!excludedSkus.has(item.sku)" class="not-in-budget">{{ t('restocking.notInBudget') }}</span>
                  <span v-else>-</span>
                </td>
                <td class="num">{{ formatPrice(item.unit_cost) }}</td>
                <td class="num">
                  <span v-if="allocatedLines[item.sku]">{{ formatPrice(allocatedLines[item.sku].lineTotal) }}</span>
                  <span v-else>-</span>
                </td>
                <td class="num">{{ t('restocking.leadTimeDays', { days: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { allocateBudget } from '../utils/restocking'
import { formatCurrency as formatCurrencyUtil, formatCurrencyWithDecimals } from '../utils/currency'

// Server category name -> locale key
const CATEGORY_KEYS = {
  'Mechanical Parts': 'categories.mechanicalParts',
  'Motors': 'categories.motors',
  'Filtration': 'categories.filtration',
  'Valves': 'categories.valves',
  'Sensors': 'categories.sensors',
  'Controllers': 'categories.controllers',
  'Power Supplies': 'categories.powerSupplies'
}

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const candidates = ref([])

    // Slider position in USD. The effective budget is clamped to maxBudget (see below).
    const sliderValue = ref(0)
    const budgetInitialized = ref(false)
    // Excluded SKUs live in a ref'd Set; it is replaced (not mutated) so reactivity is trivial.
    const excludedSkus = ref(new Set())

    const submitting = ref(false)
    const submitError = ref(null)
    const confirmation = ref(null)

    // Full restock cost: the most anyone could ever spend, so the slider's upper bound
    const maxBudget = computed(() =>
      Math.round(candidates.value.reduce((sum, c) => sum + c.estimated_cost, 0) * 100) / 100
    )

    // Step is roughly 1% of the range, as a whole number
    const sliderStep = computed(() => Math.max(1, Math.round(maxBudget.value / 100)))

    // A range input can only land on min + n * step, so with max = 28897 and step = 289 the
    // top of the track would stop at 28612 and a full restock would be unreachable. Round the
    // track length up to a whole number of steps and clamp the budget back to maxBudget.
    const sliderMax = computed(() => Math.ceil(maxBudget.value / sliderStep.value) * sliderStep.value)

    const budget = computed(() => Math.min(sliderValue.value, maxBudget.value))

    const allocation = computed(() =>
      allocateBudget(candidates.value, budget.value, excludedSkus.value)
    )

    // Lookup by SKU for the table rows
    const allocatedLines = computed(() => {
      const bySku = {}
      for (const line of allocation.value.lines) bySku[line.sku] = line
      return bySku
    })

    const formatCurrency = (value) => formatCurrencyUtil(value, currentCurrency.value)
    // Unit and line costs carry cents in USD; yen output ignores the decimals
    const formatPrice = (value) => formatCurrencyWithDecimals(value, currentCurrency.value, 2)

    const translateCategory = (category) => {
      const key = CATEGORY_KEYS[category]
      return key ? t(key) : category
    }

    const translateTrend = (trend) => {
      return ['increasing', 'stable', 'decreasing'].includes(trend) ? t(`trends.${trend}`) : trend
    }

    const toggleSku = (sku) => {
      const next = new Set(excludedSkus.value)
      if (next.has(sku)) next.delete(sku)
      else next.add(sku)
      excludedSkus.value = next
    }

    const loadCandidates = async () => {
      try {
        loading.value = candidates.value.length === 0
        error.value = null
        candidates.value = await api.getRestockCandidates()

        // Default to half of the full restock cost, snapped to the slider step; only on first load
        // so a reload after placing an order does not move the user's slider
        if (!budgetInitialized.value) {
          sliderValue.value = Math.round(maxBudget.value * 0.5 / sliderStep.value) * sliderStep.value
          budgetInitialized.value = true
        }
      } catch (err) {
        // Only take over the page on the first load. A failed refresh after placing an order
        // keeps the current list and the confirmation banner, so the user doesn't think the
        // order failed and submit it again.
        if (candidates.value.length === 0) error.value = t('restocking.loadFailed')
        console.error('Failed to load restock candidates:', err)
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      if (submitting.value || allocation.value.lines.length === 0) return
      submitting.value = true
      submitError.value = null
      confirmation.value = null
      try {
        confirmation.value = await api.createRestockOrder({
          items: allocation.value.lines.map(({ sku, quantity }) => ({ sku, quantity })),
          budget: budget.value
        })
        excludedSkus.value = new Set()
        await loadCandidates()
      } catch (err) {
        // 400/404 carry a human-readable detail; 422 detail is an array, so only show strings
        const detail = err.response?.data?.detail
        submitError.value = typeof detail === 'string'
          ? `${t('restocking.orderFailed')} ${detail}`
          : t('restocking.orderFailed')
        console.error('Failed to place restock order:', err)
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadCandidates)

    return {
      t,
      loading,
      error,
      candidates,
      sliderValue,
      sliderMax,
      sliderStep,
      maxBudget,
      budget,
      allocation,
      allocatedLines,
      excludedSkus,
      submitting,
      submitError,
      confirmation,
      formatCurrency,
      formatPrice,
      translateProductName,
      translateCategory,
      translateTrend,
      toggleSku,
      placeOrder
    }
  }
}
</script>

<style scoped>
.confirmation-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  padding: 0.875rem 1rem;
  border-radius: 8px;
  margin-bottom: 1.25rem;
  font-size: 0.938rem;
}

.confirmation-link {
  color: #166534;
  font-weight: 600;
  text-decoration: underline;
  white-space: nowrap;
}

.confirmation-link:hover {
  color: #14532d;
}

.budget-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 1rem;
  margin-bottom: 1rem;
}

.budget-label {
  font-size: 0.813rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.25rem;
}

.budget-value {
  font-size: 2.25rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
  line-height: 1.1;
}

.budget-full-cost {
  font-size: 0.875rem;
  color: #64748b;
}

.budget-slider {
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  accent-color: #2563eb;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  outline: none;
}

/* appearance: none removes the native track, so the thumb is styled explicitly */
.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.3);
}

.budget-slider::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #2563eb;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.3);
}

.budget-slider:focus-visible {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

.budget-slider:focus-visible::-webkit-slider-thumb {
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.35);
}

.budget-slider:focus-visible::-moz-range-thumb {
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.35);
}

.budget-slider:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.budget-hint {
  margin-top: 0.75rem;
  font-size: 0.813rem;
  color: #64748b;
}

.place-order-button {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 6px;
  background: #3b82f6;
  color: white;
  font-size: 0.813rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}

.place-order-button:hover:not(:disabled) {
  background: #2563eb;
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(59, 130, 246, 0.3);
}

.place-order-button:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.3);
}

.place-order-button:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}

.submit-error {
  margin-top: 0;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.restocking-table th.num,
.restocking-table td.num {
  text-align: right;
  white-space: nowrap;
}

.col-include {
  width: 70px;
  text-align: center;
}

.include-checkbox {
  accent-color: #2563eb;
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.item-name {
  font-weight: 600;
  color: #0f172a;
}

.item-sku {
  font-size: 0.813rem;
  color: #64748b;
}

/* Rows that will not be part of the order */
.restocking-table tr.muted td {
  color: #94a3b8;
}

.restocking-table tr.muted .item-name {
  color: #94a3b8;
}

.not-in-budget {
  font-size: 0.813rem;
  font-weight: 600;
  color: #b45309;
}
</style>
