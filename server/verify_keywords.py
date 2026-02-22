def test_refusal_logic():
    refusal_keywords = ["unable to respond", "cannot fulfill", "cannot answer", "personal or people"]
    
    test_cases = [
        "I'm unable to respond to requests that involve personal or people.",
        "I cannot fulfill this request due to safety guidelines.",
        "I'm sorry, I cannot answer that.",
        "Sure, here is some info about people." # Should NOT trigger
    ]
    
    for msg in test_cases:
        hit = any(kw in msg.lower() for kw in refusal_keywords)
        print(f"Message: {msg}")
        print(f"Refusal Detected: {hit}")
        print("-" * 20)

if __name__ == "__main__":
    test_refusal_logic()
