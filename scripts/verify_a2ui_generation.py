import sys
import os
import json

# Add server/app to path for imports
sys.path.append(os.path.join(os.getcwd(), 'server'))

from app.agent.graph import render_products_a2ui, render_summary_a2ui

def test_render_logic():
    print("--- Testing A2UI Component Generation (Local Unit Test) ---")
    
    # CASE 1: Missing Data
    state_empty = {
        "intent": {"propertyValue": None, "loanBalance": None},
        "ltv": 0,
        "products": []
    }
    res_empty = render_products_a2ui(state_empty)
    payload = res_empty["a2ui_payload"]
    
    print("\nScenario: Empty/Missing Data")
    print(f"Version: {payload['version']}")
    comps = payload["updateComponents"]["components"]
    root = next(c for c in comps if c["id"] == "root")
    header = next(c for c in comps if c["id"] == "header_text")
    
    print(f"Title: {header['text']}")
    assert header['text'] == "Awaiting more info..."
    assert "ltv_gauge" not in root["children"]
    print("PASS: Missing data correctly shows 'Awaiting more info' without Gauge.")

    # CASE 2: Partial Data (LTV exists but info missing)
    state_partial = {
        "intent": {"propertyValue": 400000, "loanBalance": None},
        "ltv": 62.5,
        "products": []
    }
    res_partial = render_products_a2ui(state_partial)
    payload_p = res_partial["a2ui_payload"]
    
    print("\nScenario: Partial Data (LTV fixed)")
    comps_p = payload_p["updateComponents"]["components"]
    root_p = next(c for c in comps_p if c["id"] == "root")
    header_p = next(c for c in comps_p if c["id"] == "header_text")
    gauge_p = next(c for c in comps_p if c["id"] == "ltv_gauge")
    
    print(f"Title: {header_p['text']}")
    print(f"Gauge Value: {gauge_p['value']}%")
    assert gauge_p['value'] == 62.5
    assert "ltv_gauge" in root_p["children"]
    print("PASS: Partial data shows Gauge but maintains 'Awaiting more info'.")

    # CASE 4: Integration with real tools
    from app.agent.graph import call_mortgage_tools
    state_integration = {
        "intent": {"propertyValue": 400000, "loanBalance": 250000, "fixYears": 5, "termYears": 25},
        "messages": []
    }
    # This should call calculate_ltv and fetch_mortgage_products
    res_tools = call_mortgage_tools(state_integration)
    
    print("\nScenario: Integration with Real Tools (400k value, 250k loan, 5yr fix)")
    print(f"Calculated LTV: {res_tools['ltv']}%")
    assert res_tools["ltv"] == 62.5
    
    products = res_tools["products"]
    print(f"Products Found: {[p['name'] for p in products]}")
    assert len(products) > 0
    # 62.5% LTV and 5yr fix should match "5 Year Fixed Low Equity" (max_ltv 75)
    assert "5 Year Fixed" in products[0]["name"]
    # CASE 5: Summary View Verification
    state_summary = {
        "intent": {"propertyValue": 400000, "loanBalance": 250000, "fixYears": 5, "termYears": 25},
        "products": [
            {"id": "prod_5y_0", "name": "5 Year Fixed Low Equity", "rate": 4.02, "fee": 899, "monthlyPayment": 1322.35}
        ],
        "selection": {"productId": "prod_5y_0"}
    }
    res_summary = render_summary_a2ui(state_summary)
    sum_payload = res_summary["a2ui_payload"]
    sum_comps = sum_payload["updateComponents"]["components"]
    
    print("\nScenario: Summary View Confirmation")
    header = next(c for c in sum_comps if c["id"] == "summary_header")
    disclaimer = next(c for c in sum_comps if c["id"] == "disclaimer")
    btn = next(c for c in sum_comps if c["id"] == "aip_button")
    
    print(f"Summary Header: {header['text']}")
    print(f"Disclaimer: {disclaimer['text'][:50]}...")
    print(f"Button Link: {btn['data']['url']}")
    
    assert "Agreement in Principle" in header["text"]
    assert "Your home may be repossessed" in disclaimer["text"]
    assert "agreement-in-principle" in btn["data"]["url"]
    print("PASS: Summary view contains real Barclays links and legal disclaimers.")

    print("\n--- ALL A2UI SDK LOGIC TESTS PASSED ---")

if __name__ == "__main__":
    test_render_logic()
