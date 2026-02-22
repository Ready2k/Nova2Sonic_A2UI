import json

def calculate_ltv(propertyValue: float, loanBalance: float) -> float:
    """Calculate the Loan-to-Value percentage, rounded to 1 decimal place."""
    if propertyValue <= 0:
        return 0.0
    val = (loanBalance / propertyValue) * 100
    return round(val, 1)

def recalculate_monthly_payment(principal: float, annualRate: float, termYears: int, fee: float) -> dict:
    """
    Calculate monthly payment (capital & interest), total interest, and total paid.
    Use standard amortisation formula:
    M = [P * r * (1 + r)^n] / [(1 + r)^n - 1]
    """
    if termYears <= 0 or principal <= 0 or annualRate <= 0:
        return {"monthlyPayment": 0, "totalInterest": 0, "totalPaid": 0}
        
    r = (annualRate / 100) / 12
    n = termYears * 12
    
    factor = (1 + r) ** n
    monthly_payment = (principal * r * factor) / (factor - 1)
    
    total_paid = (monthly_payment * n) + fee
    total_interest = total_paid - principal - fee
    
    return {
        "monthlyPayment": round(monthly_payment, 2),
        "totalInterest": round(total_interest, 2),
        "totalPaid": round(total_paid, 2)
    }

def fetch_mortgage_products(ltv: float, fixYears: int) -> list:
    """Return real Barclays products based on LTV and requested fix term."""
    # Data gathered from Barclays website (Feb 2026)
    products_db = [
        # 2 Year Fixed
        {"name": "2 Year Fixed Purchase", "years": 2, "max_ltv": 75, "rate": 3.76, "fee": 899},
        {"name": "2 Year Fixed Purchase (Fee-Free)", "years": 2, "max_ltv": 75, "rate": 3.91, "fee": 0},
        {"name": "2 Year Fixed Remortgage", "years": 2, "max_ltv": 60, "rate": 3.76, "fee": 999},
        {"name": "2 Year Fixed High LVT", "years": 2, "max_ltv": 95, "rate": 4.60, "fee": 0},
        
        # 3 Year Fixed
        {"name": "3 Year Fixed Standard", "years": 3, "max_ltv": 75, "rate": 3.85, "fee": 899},
        {"name": "3 Year Fixed High LVT", "years": 3, "max_ltv": 95, "rate": 4.85, "fee": 899},
        
        # 5 Year Fixed
        {"name": "5 Year Fixed Standard Purchase", "years": 5, "max_ltv": 60, "rate": 4.00, "fee": 899},
        {"name": "5 Year Fixed Low Equity", "years": 5, "max_ltv": 75, "rate": 4.02, "fee": 899},
        {"name": "5 Year Fixed High LVT (Fee-Free)", "years": 5, "max_ltv": 95, "rate": 4.71, "fee": 0},
        
        # 10 Year Fixed
        {"name": "10 Year Fixed Security", "years": 10, "max_ltv": 60, "rate": 4.95, "fee": 999},
        {"name": "10 Year Fixed High LVT", "years": 10, "max_ltv": 80, "rate": 5.51, "fee": 999},
    ]

    # Filter by fix term and LTV
    eligible = [
        p for p in products_db 
        if p["years"] == fixYears and ltv <= p["max_ltv"]
    ]
    
    # If no exact match for fix years or high LTV, show closest available products up to 2
    if not eligible:
        # First try to find products for the requested fix term regardless of LTV (if LTV is super high)
        eligible = [p for p in products_db if p["years"] == fixYears]
        # If still nothing, just take anything
        if not eligible:
            eligible = products_db[:]
        
        # Sort by LTV first (prefer higher LTV products) then by fix years proximity
        eligible.sort(key=lambda x: (abs(x["max_ltv"] - ltv), abs(x["years"] - fixYears)))
    
    # Return at most 2 products for the UI comparison
    results = []
    # Use deterministic IDs as per agent_goals.md
    id_map = {0: "prod_standard_fix", 1: "prod_premier_fix"}
    for i, p in enumerate(eligible[:2]):
        results.append({
            "id": id_map.get(i, f"prod_extra_{i}"),
            "name": p["name"],
            "rate": p["rate"],
            "fee": p["fee"]
        })
    
    return results
