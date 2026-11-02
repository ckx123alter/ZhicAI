/** Single source of truth for the fictional account used by the front-end demo. */
export const demoConfig = {
  account: {
    openingBalance: 12000,
    riskLevel: 2,
    reserveAmount: 2000,
    monthlySalary: 10000,
    salaryDay: 15,
    monthlyExpenses: 6000,
  },
  cashflow: {
    forecastDays: 30,
    dailyExpenseBasisDays: 30,
    annualizationBasisDays: 365,
  },
  ledger: {
    month: '2026-10',
    smallAmountThreshold: 30,
  },
  holdings: [
    { productId: 'D', principal: 8000, profit: 14.55, purchaseDate: '2026-09-30', holdingDays: 30 },
    { productId: 'K', principal: 5000, profit: 13.2, purchaseDate: '2026-09-16', holdingDays: 45 },
  ],
}
