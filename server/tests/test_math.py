import math
from app.agent.plugins.mortgage.tools import calculate_ltv, recalculate_monthly_payment, fetch_mortgage_products

def test_calculate_ltv():
    assert calculate_ltv(400000, 250000) == 62.5
    assert calculate_ltv(100000, 90000) == 90.0
    assert calculate_ltv(500000, 0) == 0.0

def test_amortisation_formula():
    # Example: 250k at 4.2% over 25 years = 1347.41 per month
    calc = recalculate_monthly_payment(250000, 4.2, 25, 999)
    # math checks out to ~1347.41
    assert math.isclose(calc["monthlyPayment"], 1347.41, rel_tol=1e-4)

    # 4.0%
    calc2 = recalculate_monthly_payment(250000, 4.0, 25, 1499)
    assert math.isclose(calc2["monthlyPayment"], 1319.59, rel_tol=1e-4)
    
def test_fetch_products():
    out = fetch_mortgage_products(62.5, 5)
    assert len(out) == 2
    assert out[0]["id"] == "prod_standard_fix"
    assert out[1]["id"] == "prod_premier_fix"
