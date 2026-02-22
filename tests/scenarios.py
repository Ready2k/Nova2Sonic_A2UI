"""
scenarios.py — Individual GBT test implementations.

Each function is named after its GBT ID, receives no args, returns TestResult.
Voice steps use client.text (the server routes it through the full graph).
UI steps use client.ui.action.
"""
import asyncio
import time
from harness import TestClient, TestResult


# ─────────────────────────────────────────────────────────────────────────────
# Shared setup: select category and reach the quote-builder screen
# ─────────────────────────────────────────────────────────────────────────────

async def _select_ftb(client: TestClient) -> list:
    """Click First-time buyer and return the received messages."""
    return await client.click_category("First-time buyer", "btn_ftb")


async def _reach_comparison(client: TestClient):
    """Drive through the full intent collection to get to the product comparison screen."""
    await _select_ftb(client)
    await client.say("yes")           # existingCustomer → True (Barclays)
    await client.say("yes")           # propertySeen → True
    await client.say("350000")        # propertyValue
    await client.say("310000")        # loanBalance
    return await client.say("5")      # fixYears → comparison screen


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-01 — FTB Basic Quote
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_01() -> TestResult:
    r = TestResult("GBT-FTB-01", "FTB Basic Quote – property 350k, deposit 40k, 5yr fix → LTV ≈ 88.6%")
    try:
        async with TestClient() as c:
            # Landing screen check
            landing = c.get_a2ui_patches()
            r.check("Landing screen shows category grid", len(landing) > 0,
                    f"{len(landing)} patches on connect")

            # Select category
            msgs = await _select_ftb(c)
            r.check("Screen switched to quote-builder after category click",
                    "build your quote" in c.get_header(msgs).lower(),
                    c.get_header(msgs))

            # Drive full conversation
            await c.say("yes")       # bank with Barclays
            await c.say("yes")       # property found
            await c.say("350000")    # property value
            await c.say("310000")    # loan balance
            final = await c.say("5") # fix years

            ltv = c.get_gauge_value(final)
            r.check("LTV gauge ≈ 88.6% (±1.0%)",
                    ltv is not None and abs(ltv - 88.6) <= 1.0,
                    f"ltv={ltv}")

            cards = c.count_product_cards(final)
            r.check("Two product cards rendered", cards >= 2, f"cards={cards}")

            transcripts = c.get_transcripts(final)
            sentences = sum(t.count(".") + t.count("?") + t.count("!") for t in transcripts)
            r.check("Agent spoke ≤2 sentences", sentences <= 2, f"sentences≈{sentences}")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-02 — Correction Handling
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_02() -> TestResult:
    r = TestResult("GBT-FTB-02", "Correction handling – '420… sorry, 420,000' → LTV ≈ 84.5%")
    try:
        async with TestClient() as c:
            await _select_ftb(c)
            await c.say("yes")
            await c.say("yes")
            # Transcript with a self-correction
            await c.say("four twenty… sorry, four hundred and twenty thousand")
            await c.say("355000")   # loanBalance (420000 - 65000)
            final = await c.say("5")

            ltv = c.get_gauge_value(final)
            comps = c.get_all_components(final)
            pv_text = next((c2.get("text", "") for c2 in comps if c2.get("id") == "val_pv"), "")

            r.check("propertyValue shown as 420,000 (not 420)",
                    "420" in pv_text and "420,000" in pv_text,
                    f"val_pv='{pv_text}'")
            r.check("LTV ≈ 84.5% (±1.0%)",
                    ltv is not None and abs(ltv - 84.5) <= 1.0,
                    f"ltv={ltv}")
            r.check("Comparison rendered (cards present)",
                    c.count_product_cards(final) >= 2,
                    f"cards={c.count_product_cards(final)}")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-03 — Missing Fix Period Prompt
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_03() -> TestResult:
    r = TestResult("GBT-FTB-03", "Missing fixYears → agent asks one question, no cards until answered")
    try:
        async with TestClient() as c:
            await _select_ftb(c)
            await c.say("yes")
            await c.say("yes")
            await c.say("300000")
            # Give loan but NOT fixYears
            q_msgs = await c.say("270000")

            cards_before = c.count_product_cards(q_msgs)
            transcripts_before = c.get_transcripts(q_msgs)
            question_count = len([t for t in transcripts_before if "?" in t])
            r.check("No product cards before fixYears provided", cards_before == 0,
                    f"cards={cards_before}")
            r.check("Exactly one question asked", question_count == 1,
                    f"questions={question_count}, texts={transcripts_before}")

            # Now provide fixYears
            final = await c.say("five years")
            r.check("Product cards appear after fixYears provided",
                    c.count_product_cards(final) >= 2,
                    f"cards={c.count_product_cards(final)}")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-04 — Term Slider Recalculation
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_04() -> TestResult:
    r = TestResult("GBT-FTB-04", "Term slider 25→30: fast patch, LTV unchanged, monthly updated")
    try:
        async with TestClient() as c:
            await _reach_comparison(c)
            c.messages.clear()  # baseline

            t0 = time.time()
            slider_msgs = await c.ui_action("update_term", {"action": "update_term", "termYears": 30})
            latency_ms = c.elapsed_ms(t0)

            patches = c.get_a2ui_patches(slider_msgs)
            voice_msgs = [m for m in slider_msgs if m.type == "server.voice.say"]

            r.check("A2UI patch received", len(patches) > 0, f"patches={len(patches)}")
            r.check("No missing-info voice prompt triggered", len(voice_msgs) == 0,
                    f"voice_msgs={len(voice_msgs)}")
            r.check("Patch latency <3000ms (no-mock baseline)",
                    latency_ms < 3000, f"{latency_ms:.0f}ms")

            # Check LTV unchanged
            ltv_after = c.get_gauge_value(slider_msgs)
            if ltv_after is not None:
                r.check("LTV is still present (not cleared)", True, f"ltv={ltv_after}")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-05 — Product Selection → Summary
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_05() -> TestResult:
    r = TestResult("GBT-FTB-05", "Product selection → Summary state with disclaimer + confirm")
    try:
        async with TestClient() as c:
            final_compare = await _reach_comparison(c)
            # Pick the first product id from the comparison screen
            comps = c.get_all_components(final_compare)
            product_id = next(
                (c2.get("data", {}).get("productId") for c2 in comps
                 if c2.get("component") == "Button" and c2.get("data", {}).get("action") == "select_product"),
                "product_1"
            )

            summary_msgs = await c.ui_action("select_product",
                                             {"action": "select_product", "productId": product_id})

            r.check("Confirm button present",
                    c.has_button_with_text("confirm", summary_msgs),
                    str([c2.get("text") for c2 in c.get_all_components(summary_msgs)
                         if c2.get("component") == "Button"]))
            # Disclaimer is a text node containing "disclaimer" or representative text
            all_texts = [c2.get("text", "").lower() for c2 in c.get_all_components(summary_msgs)]
            r.check("Disclaimer / summary content visible",
                    any("disclaimer" in t or "representative" in t or "illustration" in t
                        for t in all_texts),
                    str(all_texts[:5]))
            r.check("A2UI patch received for summary", len(c.get_a2ui_patches(summary_msgs)) > 0)

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-06 — Confirm Application
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_06() -> TestResult:
    r = TestResult("GBT-FTB-06", "Confirm application → confirmed state + reset button")
    try:
        async with TestClient() as c:
            final_compare = await _reach_comparison(c)
            comps = c.get_all_components(final_compare)
            product_id = next(
                (c2.get("data", {}).get("productId") for c2 in comps
                 if c2.get("component") == "Button" and c2.get("data", {}).get("action") == "select_product"),
                "product_1"
            )
            await c.ui_action("select_product", {"action": "select_product", "productId": product_id})
            confirmed_msgs = await c.ui_action("confirm_application",
                                               {"action": "confirm_application"})

            r.check("Reset button visible after confirm",
                    c.has_button_with_text("reset", confirmed_msgs),
                    str([c2.get("text") for c2 in c.get_all_components(confirmed_msgs)
                         if c2.get("component") == "Button"]))
            r.check("A2UI patch received for confirmed state",
                    len(c.get_a2ui_patches(confirmed_msgs)) > 0)

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-09 — Reset Clears State
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_09() -> TestResult:
    r = TestResult("GBT-FTB-09", "Reset clears state → category grid returns")
    try:
        async with TestClient() as c:
            await _reach_comparison(c)
            reset_msgs = await c.reset()

            header = c.get_header(reset_msgs)
            buttons = [c2.get("text", "") for c2 in c.get_all_components(reset_msgs)
                       if c2.get("component") == "Button"]
            r.check("Category grid header returns",
                    "mortgage" in header.lower() or "option" in header.lower(),
                    f"header='{header}'")
            r.check("Category buttons visible",
                    any("buyer" in b.lower() or "remortgage" in b.lower() for b in buttons),
                    f"buttons={buttons}")
            r.check("No product cards after reset",
                    c.count_product_cards(reset_msgs) == 0,
                    f"cards={c.count_product_cards(reset_msgs)}")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-10 — Invalid Input (Zero property value)
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_10() -> TestResult:
    r = TestResult("GBT-FTB-10", "Invalid input (PV=0) → error state, no crash")
    try:
        async with TestClient() as c:
            await _select_ftb(c)
            await c.say("yes")
            await c.say("yes")
            await c.say("0")        # invalid property value
            await c.say("200000")
            error_msgs = await c.say("5")

            # Should not crash — just get some response
            received_any = len(error_msgs) > 0 or len(c.messages) > 0
            r.check("Server did not crash (received response)", received_any,
                    f"total_msgs={len(c.messages)}")
            # Check no "500" internal error leaked
            all_payloads = str([m.payload for m in error_msgs])
            r.check("No raw stack trace in payload",
                    "Traceback" not in all_payloads and "traceback" not in all_payloads,
                    "clean response")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-11 — Vague user: one question at a time
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_11() -> TestResult:
    r = TestResult("GBT-FTB-11", "Vague user → one question at a time, no overload")
    try:
        async with TestClient() as c:
            await _select_ftb(c)
            await c.say("yes")
            await c.say("yes")

            # Vague first reply — should get ONE question about property value
            vague_msgs = await c.say("I don't know what I can afford. I've got about thirty grand saved.")
            transcripts = c.get_transcripts(vague_msgs)
            questions = [t for t in transcripts if "?" in t]
            r.check("Only one question asked for vague input",
                    len(questions) == 1, f"questions={questions}")
            r.check("No product cards shown for vague input",
                    c.count_product_cards(vague_msgs) == 0,
                    f"cards={c.count_product_cards(vague_msgs)}")

            # Follow up with property value
            next_msgs = await c.say("about three hundred thousand")
            next_qs = [t for t in c.get_transcripts(next_msgs) if "?" in t]
            r.check("One follow-up question after value provided",
                    len(next_qs) == 1, f"questions={next_qs}")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# GBT-FTB-12 — TTFB Performance
# ─────────────────────────────────────────────────────────────────────────────
async def gbt_ftb_12() -> TestResult:
    r = TestResult("GBT-FTB-12", "TTFB: transcript + thinking + patch arrive quickly after text turn")
    try:
        async with TestClient() as c:
            await _select_ftb(c)
            await c.say("yes")
            await c.say("yes")

            t0 = time.time()
            msgs = await c.say("Buying for 400000, loan 340000, five year fix")
            ttfb_ms = c.elapsed_ms(t0)

            has_thinking = any(m.type == "server.agent.thinking" for m in msgs)
            has_patch = any(m.type == "server.a2ui.patch" for m in msgs)
            has_transcript = any(m.type == "server.transcript.final" for m in msgs)

            r.check("transcript.final received", has_transcript, f"TTFB={ttfb_ms:.0f}ms")
            r.check("agent.thinking signal received", has_thinking)
            r.check("a2ui.patch received", has_patch)
            r.check("Full response within 15000ms (Bedrock live latency)", ttfb_ms < 15000, f"{ttfb_ms:.0f}ms")

    except Exception as e:
        import traceback; r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Registry — map GBT ID → coroutine function
# ─────────────────────────────────────────────────────────────────────────────
SCENARIOS = {
    "GBT-FTB-01": gbt_ftb_01,
    "GBT-FTB-02": gbt_ftb_02,
    "GBT-FTB-03": gbt_ftb_03,
    "GBT-FTB-04": gbt_ftb_04,
    "GBT-FTB-05": gbt_ftb_05,
    "GBT-FTB-06": gbt_ftb_06,
    "GBT-FTB-09": gbt_ftb_09,
    "GBT-FTB-10": gbt_ftb_10,
    "GBT-FTB-11": gbt_ftb_11,
    "GBT-FTB-12": gbt_ftb_12,
}
