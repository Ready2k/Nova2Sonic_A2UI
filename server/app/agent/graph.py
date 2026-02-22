import os
import logging
from typing import Dict, Any, List, Optional, TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field
from .tools import calculate_ltv, fetch_mortgage_products, recalculate_monthly_payment
from geopy.geocoders import Nominatim

logger = logging.getLogger(__name__)

def append_reducer(a: list, b: list) -> list:
    return a + b

class AgentState(TypedDict):
    mode: str
    transcript: str
    messages: Annotated[List[Dict[str, Any]], append_reducer]
    intent: Dict[str, Any]
    ltv: float
    products: List[Dict[str, Any]]
    selection: Dict[str, Any]
    ui: Dict[str, Any]
    errors: Optional[Dict[str, Any]]
    pendingAction: Optional[Dict[str, Any]]
    outbox: Annotated[List[Dict[str, Any]], append_reducer]
    existing_customer: Optional[bool]
    property_seen: Optional[bool]

def ingest_input(state: AgentState):
    logger.info(f"NODE: ingest_input - transcript='{state.get('transcript')}'")
    return {} 

class MortgageIntent(BaseModel):
    propertyValue: Optional[int] = Field(description="The value of the property in GBP", default=None)
    loanBalance: Optional[int] = Field(description="The remaining loan balance in GBP", default=None)
    fixYears: Optional[int] = Field(description="The requested fixed rate term in years, e.g. 2, 5, 10", default=None)
    termYears: Optional[int] = Field(description="The overall mortgage repayment term in years, default is 25", default=25)
    existingCustomer: Optional[bool] = Field(description="Whether the user already banks with Barclays", default=None)
    propertySeen: Optional[bool] = Field(description="Whether the user has already found a property they want to buy", default=None)
    address: Optional[str] = Field(description="The address of the property", default=None)
    lat: Optional[float] = Field(description="Latitude of the property", default=None)
    lng: Optional[float] = Field(description="Longitude of the property", default=None)

def interpret_intent(state: AgentState):
    transcript = state.get("transcript", "").strip()
    logger.info(f"NODE: interpret_intent - input='{transcript}'")
    intent = state.get("intent", {}) or {}
    messages = state.get("messages", [])
    
    if not transcript:
        return {}

    # Determine what question was last asked (so we can interpret short answers like "yes" correctly)
    last_question_context = ""
    if intent.get("existingCustomer") is None:
        last_question_context = "The last question asked was: 'Do you already bank with Barclays?' — so 'yes'/'yes it is'/'yeah' means existingCustomer=true, 'no'/'nope' means existingCustomer=false."
    elif intent.get("propertySeen") is None:
        last_question_context = "The last question asked was: 'Have you found a property yet?' — so 'yes'/'yeah'/'found one' means propertySeen=true, 'no'/'not yet' means propertySeen=false."
    elif not intent.get("propertyValue"):
        last_question_context = "The last question asked was about property value. Extract the number from the answer."
    elif not intent.get("loanBalance"):
        last_question_context = "The last question asked was about loan amount. Extract the number from the answer."
    elif not intent.get("fixYears"):
        last_question_context = "The last question asked was about fixed term years (2, 3, 5, or 10). Extract the number."

    if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
        try:
            from langchain_aws import ChatBedrockConverse
            from langchain_core.messages import HumanMessage
            
            model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
            llm = ChatBedrockConverse(
                model=model_id, 
                region_name=os.getenv("AWS_REGION", "us-east-1")
            )
            structured_llm = llm.with_structured_output(MortgageIntent)
            
            lc_messages = []
            for msg in messages[:-2]:  # exclude last 2 which are already in the prompt  
                if msg.get("role") == "user":
                    lc_messages.append(HumanMessage(content=msg.get("text", "")))
                        
            current_prompt = (
                f"Extract mortgage details from the user's latest response.\n"
                f"Context: {last_question_context}\n"
                f"Current known intent: {intent}\n"
                f"User just said: '{transcript}'\n\n"
                "Rules:\n"
                "- Interpret short answers (yes/no/yeah/nope) using the context above.\n"
                "- For money amounts, extract the number (e.g. '400k' = 400000, '400 thousand' = 400000).\n"
                "- Do NOT change fields that already have values unless the user explicitly corrects them.\n"
                "- If the user is just being conversational ('okay', 'go for it'), do NOT change any fields.\n"
            )
            lc_messages.append(HumanMessage(content=current_prompt))
            
            result = structured_llm.invoke(lc_messages)
            idict = result.model_dump(exclude_none=True)
            
            new_intent = {**intent, **idict}
            
            return {
                "intent": new_intent,
                "existing_customer": new_intent.get("existingCustomer"),
                "property_seen": new_intent.get("propertySeen")
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Fallback to mock parsing due to Bedrock error: {e}")

    # Keyword fallback (no AWS)
    new_intent = dict(intent)
    t = transcript.lower()
    if intent.get("existingCustomer") is None:
        if any(w in t for w in ["yes", "yeah", "yep", "do", "i am", "i do", "it is"]):
            new_intent["existingCustomer"] = True
        elif any(w in t for w in ["no", "nope", "don't", "dont", "not"]):
            new_intent["existingCustomer"] = False
    elif intent.get("propertySeen") is None:
        if any(w in t for w in ["yes", "yeah", "found", "seen", "have"]):
            new_intent["propertySeen"] = True
        elif any(w in t for w in ["no", "nope", "not yet", "haven't"]):
            new_intent["propertySeen"] = False

    return {
        "intent": new_intent,
        "existing_customer": new_intent.get("existingCustomer"),
        "property_seen": new_intent.get("propertySeen")
    }

def render_missing_inputs(state: AgentState):
    intent = state.get("intent", {})
    missing = []
    
    category = intent.get("category")
    
    if not category: missing.append("category")
    elif intent.get("existingCustomer") is None: missing.append("whether you already bank with Barclays")
    
    # Conditional logic: Remortgages don't need to 'find' a property
    elif category == "Remortgage":
        if not intent.get("address"): missing.append("address")
        elif not intent.get("propertyValue"): missing.append("property value")
        elif not intent.get("loanBalance"): missing.append("loan balance")
        elif not intent.get("fixYears"): missing.append("fixed term (years)")
    
    # Purchase-style flows (FTB, Moving Home, BTL)
    else:
        if intent.get("propertySeen") is None: missing.append("if you have already found a property")
        elif intent.get("propertySeen") and not intent.get("address"): missing.append("address")
        elif not intent.get("propertyValue"): missing.append("property value")
        elif not intent.get("loanBalance"): missing.append("loan balance")
        elif not intent.get("fixYears"): missing.append("fixed term (years)")

    new_outbox = []
    new_messages = []
    
    # Map each missing field to a short, single-sentence question.
    # When a category was just selected, prefix with a brief acknowledgment.
    category = state.get("intent", {}).get("category", "a mortgage")
    just_selected = state.get("pendingAction", {}) and state.get("pendingAction", {}).get("data", {}).get("action") == "select_category"
    
    if missing:
        logger.info(f"NODE: render_missing_inputs - missing={missing}, transcript='{state.get('transcript')}'")
        
        target_field = missing[0]
        
        # Intelligent generation via Nova 2 Lite
        if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
            try:
                from langchain_aws import ChatBedrockConverse
                from langchain_core.messages import HumanMessage, SystemMessage
                
                model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
                llm = ChatBedrockConverse(model=model_id, region_name=os.getenv("AWS_REGION", "us-east-1"))
                
                system_prompt = (
                    "You are a helpful Barclays Mortgage Assistant. Your goal is to ask the user for a specific piece of information "
                    "in a natural, conversational way. Be brief (1-2 sentences). "
                    f"Current journey stage: {category}. "
                )
                
                user_msg = f"Conversation history: {messages[-4:]}\n"
                user_msg += f"The user just said: '{state.get('transcript')}'\n"
                
                if target_field == "category":
                    user_msg += "Nudge the user to select one of the mortgage categories shown on screen."
                else:
                    user_msg += f"I need to find out: {target_field}. Ask the user for this information, acknowledging what they just said if appropriate."

                response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
                msg = response.content
            except Exception as e:
                logger.error(f"LLM generation error: {e}")
                # Fallback to static if LLM fails
                msg = f"Can you tell me your {target_field}?"
        else:
            msg = f"Can you tell me your {target_field}?"

        new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
        new_messages.append({"role": "assistant", "text": msg})
        
    intent = state.get("intent", {})
    category = intent.get("category")
    
    if not category:
        try:
            with open("/Users/jamescregeen/A2UI_S2S/ftb_b64.txt", "r") as f: ftb_icon = f.read().strip()
            with open("/Users/jamescregeen/A2UI_S2S/remortgage_b64.txt", "r") as f: remortgage_icon = f.read().strip()
            with open("/Users/jamescregeen/A2UI_S2S/btl_b64.txt", "r") as f: btl_icon = f.read().strip()
            with open("/Users/jamescregeen/A2UI_S2S/moving_b64.txt", "r") as f: moving_icon = f.read().strip()
        except:
            ftb_icon = remortgage_icon = btl_icon = moving_icon = ""

        components = [
            {"id": "root", "component": "Column", "children": ["header", "options_grid"]},
            {"id": "header", "component": "Text", "text": "Your mortgage options", "variant": "h2"},
            {"id": "options_grid", "component": "Column", "children": ["row_1", "row_2"]},
            {"id": "row_1", "component": "Row", "children": ["opt_ftb", "opt_remortgage"]},
            {"id": "row_2", "component": "Row", "children": ["opt_btl", "opt_moving"]},
            {"id": "opt_ftb", "component": "Column", "children": ["img_ftb", "btn_ftb"]},
            {"id": "img_ftb", "component": "Image", "data": {"url": f"data:image/png;base64,{ftb_icon}"}, "text": "FTB"},
            {"id": "btn_ftb", "component": "Button", "text": "First-time buyer", "data": {"action": "select_category", "category": "First-time buyer"}},
            {"id": "opt_remortgage", "component": "Column", "children": ["img_remortgage", "btn_remortgage"]},
            {"id": "img_remortgage", "component": "Image", "data": {"url": f"data:image/png;base64,{remortgage_icon}"}, "text": "Remortgage"},
            {"id": "btn_remortgage", "component": "Button", "text": "Remortgage", "data": {"action": "select_category", "category": "Remortgage"}},
            {"id": "opt_btl", "component": "Column", "children": ["img_btl", "btn_btl"]},
            {"id": "img_btl", "component": "Image", "data": {"url": f"data:image/png;base64,{btl_icon}"}, "text": "BTL"},
            {"id": "btn_btl", "component": "Button", "text": "Buy-to-let", "data": {"action": "select_category", "category": "Buy-to-let"}},
            {"id": "opt_moving", "component": "Column", "children": ["img_moving", "btn_moving"]},
            {"id": "img_moving", "component": "Image", "data": {"url": f"data:image/png;base64,{moving_icon}"}, "text": "Moving"},
            {"id": "btn_moving", "component": "Button", "text": "Moving home", "data": {"action": "select_category", "category": "Moving home"}}
        ]
        payload = {"version": "v0.9", "updateComponents": {"surfaceId": "main", "components": components}}
        new_outbox.append({"type": "server.a2ui.patch", "payload": payload})
        ui_state = dict(state.get("ui", {}))
        ui_state["state"] = "LOADING"
        return {"outbox": new_outbox, "ui": ui_state, "messages": new_messages, "transcript": ""}
        
    pv = intent.get("propertyValue")
    lb = intent.get("loanBalance")
    fy = intent.get("fixYears")
    addr = intent.get("address")
    
    pv_text = f"£{pv:,}" if pv else "Pending..."
    lb_text = f"£{lb:,}" if lb else "Pending..."
    fy_text = f"{fy} Years" if fy else "Pending..."
    addr_text = addr if addr else "N/A"

    # Determine which field the agent is currently asking about (first missing one)
    next_missing = missing[0] if missing else None
    
    pv_focus = next_missing in ("property value",)
    lb_focus = next_missing in ("loan balance",)
    fy_focus = next_missing in ("fixed term (years)",)
    addr_focus = next_missing in ("address",)
    
    # Existing-customer / propertySeen are asked verbally only, no dedicated field row
    # But we can show all three rows always, highlighting the relevant one.
    category_label = f"[{category}]" if category else ""
    
    components = [
        {"id": "root", "component": "Column", "children": ["journey", "header", "details_col"]},
        {"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 1}},
        {"id": "header", "component": "Text", "text": f"Let\u2019s build your quote {category_label}", "variant": "h2"},
        {"id": "details_col", "component": "Column", "children": ["row_addr", "row_pv", "row_lb", "row_fy"]},
        
        {"id": "row_addr", "component": "Row", "children": ["lbl_addr", "val_addr"]},
        {"id": "lbl_addr", "component": "Text", "text": "Property Address:", "variant": "h3", "focus": addr_focus},
        {"id": "val_addr", "component": "Text", "text": addr_text, "variant": "body", "focus": addr_focus},

        {"id": "row_pv", "component": "Row", "children": ["lbl_pv", "val_pv"]},
        {"id": "lbl_pv", "component": "Text", "text": "Property Value:", "variant": "h3", "focus": pv_focus},
        {"id": "val_pv", "component": "Text", "text": pv_text, "variant": "body", "focus": pv_focus},
        
        {"id": "row_lb", "component": "Row", "children": ["lbl_lb", "val_lb"]},
        {"id": "lbl_lb", "component": "Text", "text": "Loan Balance:", "variant": "h3", "focus": lb_focus},
        {"id": "val_lb", "component": "Text", "text": lb_text, "variant": "body", "focus": lb_focus},
        
        {"id": "row_fy", "component": "Row", "children": ["lbl_fy", "val_fy"]},
        {"id": "lbl_fy", "component": "Text", "text": "Fixed Term:", "variant": "h3", "focus": fy_focus},
        {"id": "val_fy", "component": "Text", "text": fy_text, "variant": "body", "focus": fy_focus},
    ]

    lat = intent.get("lat")
    lng = intent.get("lng")

    if addr and (lat is None or lng is None):
        try:
            geolocator = Nominatim(user_agent="barclays_mortgage_demo")
            location = geolocator.geocode(addr)
            if location:
                lat = location.latitude
                lng = location.longitude
                intent["lat"] = lat
                intent["lng"] = lng
                logger.info(f"Geocoding success: {addr} -> ({lat}, {lng})")
            else:
                logger.warning(f"Geocoding failed (no result): {addr}")
        except Exception as e:
            logger.error(f"Geocoding error: {e}")

    if addr:
        # Insights only show if we have an address
        insights = [
            {"label": "Energy Rating", "value": "EPC: B (Verified)"},
            {"label": "Council Tax", "value": "Band D (\u00a31,840/yr)"}
        ]
        components.insert(2, {"id": "prop_insights", "component": "DataCard", "data": {"items": insights}})
        components[0]["children"].insert(2, "prop_insights")

        # Green Mortgage Showcase
        components.insert(3, {
            "id": "green_reward", 
            "component": "BenefitCard", 
            "variant": "Green Home Reward",
            "text": "You qualify for \u00a3250 Cashback",
            "data": {"detail": "Because this property has an EPC rating of B, you're eligible for our Green Home mortgage reward."}
        })
        components[0]["children"].insert(3, "green_reward")
        
        # Add map
        map_data = {"address": addr}
        if lat and lng:
            map_data.update({"lat": lat, "lng": lng})
        
        components.insert(3, {"id": "map_view", "component": "Map", "text": addr, "data": map_data})
        components[0]["children"].insert(3, "map_view")
    
    payload = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }
    
    new_outbox.append({"type": "server.a2ui.patch", "payload": payload})
        
    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "LOADING" 
    
    return {"outbox": new_outbox, "ui": ui_state, "messages": new_messages, "transcript": "", "intent": intent}

def call_mortgage_tools(state: AgentState):
    intent = state.get("intent", {})
    pv = intent.get("propertyValue")
    lb = intent.get("loanBalance")
    fy = intent.get("fixYears")
    
    if pv is None or lb is None:
        return {"ltv": 0.0, "products": []}
    
    ltv = calculate_ltv(pv, lb)
    products = fetch_mortgage_products(ltv, fy or 5)
    
    ty = intent.get("termYears", 25)
    for p in products:
        calc = recalculate_monthly_payment(lb, p["rate"], ty, p["fee"])
        p.update(calc)
        
    return {"ltv": ltv, "products": products}

def render_products_a2ui(state: AgentState):
    ltv = state.get("ltv", 0)
    products = state.get("products", [])
    new_outbox = []
    new_messages = []

    components = [
        {"id": "root", "component": "Column", "children": ["journey", "header_text"]}
    ]
    components.append({"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 2}})
    components.append({"id": "header_text", "component": "Text", "text": "Your Comparative Analysis", "variant": "h2"})

    if ltv > 0:
        components[0]["children"].append("ltv_gauge")
        components.append({"id": "ltv_gauge", "component": "Gauge", "value": ltv, "max": 100})

    if products:
        components[0]["children"].append("market_insight")
        components.append({"id": "market_insight", "component": "ComparisonBadge", "text": "Market Leading: These rates are in the top 5% for your LTV tier"})

        components[0]["children"].append("products_row")
        components.append({"id": "products_row", "component": "Row", "children": [f"prod_{i}" for i in range(len(products))]})
        for i, p in enumerate(products):
            components.append({"id": f"prod_{i}", "component": "ProductCard", "data": p})

        # Add a final breakdown card
        breakdown = [
            {"label": "Capital Repayment", "value": f"\u00a3{int((products[0].get('monthlyPayment', 0)) * 0.4):,} (Est.)"},
            {"label": "Interest Portion", "value": f"\u00a3{int((products[0].get('monthlyPayment', 0)) * 0.6):,} (Est.)"}
        ]
        components[0]["children"].append("pmt_breakdown")
        components.append({"id": "pmt_breakdown", "component": "DataCard", "data": {"items": breakdown}})
            
    payload = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }
    
    new_outbox.append({"type": "server.a2ui.patch", "payload": payload})
    
    msg = ""
    # Intelligent generation via Nova 2 Lite
    if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
        try:
            from langchain_aws import ChatBedrockConverse
            from langchain_core.messages import HumanMessage, SystemMessage
            
            model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
            llm = ChatBedrockConverse(model=model_id, region_name=os.getenv("AWS_REGION", "us-east-1"))
            
            system_prompt = (
                "You are a helpful Barclays Mortgage Assistant. The user has provided all their details, "
                "and you have found some mortgage products for them. Acknowledge their effort and "
                "introduce the products shown on screen. Be brief (1-2 sentences)."
            )
            
            user_msg = f"User Intent: {state.get('intent')}\n"
            user_msg += f"Calculated LTV: {ltv}%\n"
            user_msg += f"Number of products found: {len(products)}\n"
            
            response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
            msg = response.content
        except Exception as e:
            logger.error(f"LLM product intro generation error: {e}")
            msg = f"Based on a {ltv}% LTV, I’ve found some {state.get('intent', {}).get('fixYears', 5)}-year options for you."
    else:
        msg = f"Based on a {ltv}% LTV, I’ve found some {state.get('intent', {}).get('fixYears', 5)}-year options for you."

    if state.get("ui", {}).get("state") != "COMPARISON":
        # Always nudge in text log if new products shown
        new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
        new_messages.append({"role": "assistant", "text": msg})
    
    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "COMPARISON"
    
    return {"outbox": new_outbox, "ui": ui_state, "messages": new_messages, "transcript": ""}

def recalculate_and_patch(state: AgentState):
    intent = state.get("intent", {})
    lb = intent.get("loanBalance")
    products = state.get("products", [])
    ty = intent.get("termYears", 25)
    
    new_outbox = []
    
    for p in products:
        calc = recalculate_monthly_payment(lb, p["rate"], ty, p["fee"])
        p.update(calc)
        
    components = []
    for i, p in enumerate(products):
        components.append({"id": f"prod_{i}", "component": "ProductCard", "data": p})
        
    payload = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }
    
    new_outbox.append({"type": "server.a2ui.patch", "payload": payload})
    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "COMPARISON"
    
    return {"outbox": new_outbox, "ui": ui_state, "products": products, "transcript": ""}

def handle_ui_action(state: AgentState):
    action = state.get("pendingAction")
    if not action:
        return {}
    
    action_id = action.get("id")
    data = action.get("data", {})
    if isinstance(data, dict) and data.get("action"):
        action_id = data.get("action")
    
    intent = dict(state.get("intent", {}))
    selection = dict(state.get("selection", {}))
    
    if action_id == "reset_flow":
        return {
            "intent": {"propertyValue": None, "loanBalance": None, "fixYears": None, "termYears": 25, "category": None},
            "selection": {},
            "products": [],
            "ltv": 0.0,
            "errors": None,
            "transcript": "",
            "existing_customer": None,
            "property_seen": None
        }
    elif action_id == "select_category":
        category = data.get("category")
        intent["category"] = category
        # No voice here — render_missing_inputs will emit the combined greeting+first question
        return {"intent": intent}
    elif action_id == "update_term":
        intent["termYears"] = data.get("termYears", intent.get("termYears", 25))
        selection["termYears"] = intent["termYears"]
        return {"intent": intent, "selection": selection}
    elif action_id == "select_product":
        selection["productId"] = data.get("productId")
        return {"selection": selection}
    elif action_id == "confirm_application":
        selection["confirmed"] = True
        return {"selection": selection}
    
    return {}

def clear_pending_action(state: AgentState):
    return {"pendingAction": None}

def render_summary_a2ui(state: AgentState):
    selection = state.get("selection", {})
    product_id = selection.get("productId")
    products = state.get("products", [])
    selected_prod = next((p for p in products if p["id"] == product_id), None)
    new_outbox = []
    new_messages = []
    
    components = [
        {"id": "root", "component": "Column", "children": ["journey", "summary_header", "summary_card", "disclaimer", "aip_button"]},
        {"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 3}},
        {"id": "summary_header", "component": "Text", "text": "Your Agreement in Principle (AiP)", "variant": "h2"},
        {"id": "summary_card", "component": "ProductCard", "data": selected_prod or (products[0] if products else {})},
        {"id": "disclaimer", "component": "Text", "text": "Your home may be repossessed if you do not keep up repayments on your mortgage. Overall cost for comparison: 5.6% APRC Representative.", "variant": "body"},
        {"id": "aip_button", "component": "Button", "text": "Confirm Application", "data": {"action": "confirm_application"}}
    ]
    
    payload = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }
    new_outbox.append({"type": "server.a2ui.patch", "payload": payload})
    
    msg = "Great choice. I've prepared your summary. You can review it on screen and confirm if you want to proceed."
    new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
    new_messages.append({"role": "assistant", "text": msg})
    
    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "SUMMARY"
    
    return {"outbox": new_outbox, "ui": ui_state, "messages": new_messages, "transcript": ""}

def confirm_application(state: AgentState):
    new_outbox = []
    new_messages = []
    components = [
        {"id": "root", "component": "Column", "children": ["journey", "confirmed_header", "reset_button"]},
        {"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 4}},
        {"id": "confirmed_header", "component": "Text", "text": "Application Started", "variant": "h1"},
        {"id": "reset_button", "component": "Button", "text": "Reset Flow", "data": {"action": "reset_flow"}}
    ]
    payload = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }
    new_outbox.append({"type": "server.a2ui.patch", "payload": payload})
    msg = "Fantastic, your application has been started successfully."
    new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
    new_messages.append({"role": "assistant", "text": msg})
    
    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "CONFIRMED"
    return {"outbox": new_outbox, "ui": ui_state, "messages": new_messages, "transcript": ""}

def root_router(state: AgentState):
    if state.get("pendingAction"):
        return "handle_ui_action"
    
    intent = state.get("intent", {})
    if intent.get("propertyValue") is None or intent.get("loanBalance") is None or intent.get("fixYears") is None:
        return "render_missing_inputs"
    
    return "call_mortgage_tools"

def ui_action_router(state: AgentState):
    action = state.get("pendingAction", {})
    if not action:
        return END
    action_id = action.get("id")
    data = action.get("data", {})
    # Allow button data to override the action id (e.g. btn_ftb has data.action = "select_category")
    if isinstance(data, dict) and data.get("action"):
        action_id = data.get("action")
    if action_id == "update_term":
        return "recalculate_and_patch"
    elif action_id == "select_product":
        return "render_summary_a2ui"
    elif action_id == "confirm_application":
        return "confirm_application"
    elif action_id in ("reset_flow", "select_category"):
        return "render_missing_inputs"
    return "clear_pending_action"

def start_router(state: AgentState):
    if state.get("pendingAction"):
        return "handle_ui_action"
    if state.get("transcript"):
        return "interpret_intent"
    
    intent = state.get("intent", {})
    if not intent.get("category") or intent.get("propertyValue") is None or intent.get("loanBalance") is None or intent.get("fixYears") is None:
        return "render_missing_inputs"
    return "call_mortgage_tools"

def intent_router(state: AgentState):
    intent = state.get("intent", {})
    if intent.get("propertyValue") is None or intent.get("loanBalance") is None or intent.get("fixYears") is None:
        return "render_missing_inputs"
    return "call_mortgage_tools"

workflow = StateGraph(AgentState)

workflow.add_node("ingest_input", ingest_input)
workflow.add_node("interpret_intent", interpret_intent)
workflow.add_node("call_mortgage_tools", call_mortgage_tools)
workflow.add_node("render_products_a2ui", render_products_a2ui)
workflow.add_node("render_missing_inputs", render_missing_inputs)
workflow.add_node("handle_ui_action", handle_ui_action)
workflow.add_node("recalculate_and_patch", recalculate_and_patch)
workflow.add_node("render_summary_a2ui", render_summary_a2ui)
workflow.add_node("confirm_application", confirm_application)
workflow.add_node("clear_pending_action", clear_pending_action)

workflow.add_edge(START, "ingest_input")
workflow.add_conditional_edges("ingest_input", start_router)
workflow.add_conditional_edges("interpret_intent", intent_router)

workflow.add_edge("render_missing_inputs", "clear_pending_action")

workflow.add_edge("call_mortgage_tools", "render_products_a2ui")
workflow.add_edge("render_products_a2ui", "clear_pending_action")

workflow.add_conditional_edges("handle_ui_action", ui_action_router)

workflow.add_edge("recalculate_and_patch", "clear_pending_action")
workflow.add_edge("render_summary_a2ui", "clear_pending_action")
workflow.add_edge("confirm_application", "clear_pending_action")

workflow.add_edge("clear_pending_action", END)

app_graph = workflow.compile()
