import os
from typing import Dict, Any, List, Optional, TypedDict
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field
from .tools import calculate_ltv, fetch_mortgage_products, recalculate_monthly_payment

class AgentState(TypedDict):
    mode: str
    transcript: str
    messages: List[Dict[str, Any]]
    intent: Dict[str, Any]
    ltv: float
    products: List[Dict[str, Any]]
    selection: Dict[str, Any]
    a2ui_payload: Optional[Dict[str, Any]]
    errors: Optional[str]
    last_event: Optional[str]
    existing_customer: Optional[bool]
    property_seen: Optional[bool]

def ingest_input(state: AgentState):
    return state 

class MortgageIntent(BaseModel):
    propertyValue: Optional[int] = Field(description="The value of the property in GBP", default=None)
    loanBalance: Optional[int] = Field(description="The remaining loan balance in GBP", default=None)
    fixYears: Optional[int] = Field(description="The requested fixed rate term in years, e.g. 2, 5, 10", default=None)
    termYears: Optional[int] = Field(description="The overall mortgage repayment term in years, default is 25", default=25)
    existingCustomer: Optional[bool] = Field(description="Whether the user already banks with Barclays", default=None)
    propertySeen: Optional[bool] = Field(description="Whether the user has already found a property they want to buy", default=None)

def interpret_intent(state: AgentState):
    transcript = state.get("transcript", "").lower()
    intent = state.get("intent", {}) or {}
    messages = state.get("messages", [])
    
    if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
        try:
            # Import inline to avoid hard dependency exception if not installed
            from langchain_aws import ChatBedrockConverse
            from langchain_core.messages import HumanMessage
            
            model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
            llm = ChatBedrockConverse(
                model=model_id, 
                region_name=os.getenv("AWS_REGION", "us-east-1")
            )
            structured_llm = llm.with_structured_output(MortgageIntent)
            
            # Format history for LangChain
            lc_messages = []
            for msg in messages:
                if msg.get("role") == "user":
                    if "image" in msg:
                        lc_messages.append(HumanMessage(content=[
                            {"type": "text", "text": msg.get("text", "")},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{msg['image']}"}}
                        ]))
                    else:
                        lc_messages.append(HumanMessage(content=msg.get("text", "")))
                        
            current_prompt = (
                "Extract mortgage details from the following user transcript. \n"
                "STRICT RULES:\n"
                "1. Only set existingCustomer if the user definitively confirms they bank with Barclays.\n"
                "2. Only set propertySeen if the user definitively says they have found a property.\n"
                "3. If the user just says 'Hi' or generic greetings, do NOT extract values for these fields; leave them as null.\n"
                "4. Do NOT assume or guess based on conversational flow.\n\n"
                f"Current Intent: {intent}\nTranscript: {transcript}"
            )
            lc_messages.append(HumanMessage(content=current_prompt))
            
            result = structured_llm.invoke(lc_messages)
            idict = result.model_dump(exclude_none=True)
            
            # Merge with existing intent
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

    # Mock conversational intent
    new_intent = dict(intent)
    if "yes" in transcript and ("bank" in transcript or "account" in transcript):
        new_intent["existingCustomer"] = True
    if "no" in transcript and ("bank" in transcript or "account" in transcript):
        new_intent["existingCustomer"] = False
    if ("found" in transcript or "seen" in transcript) and "property" in transcript:
        new_intent["propertySeen"] = True

    return {
        "intent": new_intent,
        "existing_customer": new_intent.get("existingCustomer"),
        "property_seen": new_intent.get("propertySeen")
    }

def call_mortgage_tools(state: AgentState):
    intent = state.get("intent", {})
    pv = intent.get("propertyValue")
    lb = intent.get("loanBalance")
    fy = intent.get("fixYears")
    
    # If core data is missing, we skip tool calculations and just return current state
    if pv is None or lb is None:
        return {"ltv": 0.0, "products": []}
    
    ltv = calculate_ltv(pv, lb)
    products = fetch_mortgage_products(ltv, fy or 5)
    
    ty = intent.get("termYears", 25)
    for p in products:
        calc = recalculate_monthly_payment(lb, p["rate"], ty, p["fee"])
        p.update(calc)
        
    return {"ltv": ltv, "products": products}

def _build_compliant_a2ui(state: AgentState, title: str, show_products: bool = False) -> Dict[str, Any]:
    ltv = state.get("ltv", 0)
    products = state.get("products", []) if show_products else []
    
    components = [
        {
            "id": "root",
            "component": "Column",
            "children": ["header_text"]
        },
        {
            "id": "header_text",
            "component": "Text",
            "text": title,
            "variant": "h2"
        }
    ]

    if ltv > 0:
        components[0]["children"].append("ltv_gauge")
        components.append({
            "id": "ltv_gauge",
            "component": "Gauge",
            "value": ltv,
            "max": 100
        })

    if show_products and products:
        components[0]["children"].append("products_row")
        components.append({
            "id": "products_row",
            "component": "Row",
            "children": [f"prod_{i}" for i in range(len(products))]
        })
        for i, p in enumerate(products):
            components.append({
                "id": f"prod_{i}",
                "component": "ProductCard",
                "data": p
            })

    return {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }

def render_products_a2ui(state: AgentState):
    intent = state.get("intent", {})
    missing = []
    if not intent.get("propertyValue"): missing.append("property value")
    if not intent.get("loanBalance"): missing.append("loan balance")
    if not intent.get("fixYears"): missing.append("fixed term (years)")
    
    if not missing:
        payload = _build_compliant_a2ui(state, "Your Comparative Analysis", show_products=True)
    else:
        # Show a Barclays-style "Mortgage Options" Dashboard
        # Base64 icons are read from the temporary files created earlier
        try:
            with open("/Users/jamescregeen/A2UI_S2S/ftb_b64.txt", "r") as f: ftb_icon = f.read().strip()
            with open("/Users/jamescregeen/A2UI_S2S/remortgage_b64.txt", "r") as f: remortgage_icon = f.read().strip()
            with open("/Users/jamescregeen/A2UI_S2S/btl_b64.txt", "r") as f: btl_icon = f.read().strip()
            with open("/Users/jamescregeen/A2UI_S2S/moving_b64.txt", "r") as f: moving_icon = f.read().strip()
        except Exception:
            # Fallback if files aren't found
            ftb_icon = remortgage_icon = btl_icon = moving_icon = ""

        components = [
            {"id": "root", "component": "Column", "children": ["header", "options_grid"]},
            {"id": "header", "component": "Text", "text": "Your mortgage options", "variant": "h2"},
            {
                "id": "options_grid", 
                "component": "Column", 
                "children": ["row_1", "row_2"]
            },
            {
                "id": "row_1",
                "component": "Row",
                "children": ["opt_ftb", "opt_remortgage"]
            },
            {
                "id": "row_2",
                "component": "Row",
                "children": ["opt_btl", "opt_moving"]
            },
            # First-time buyer
            {
                "id": "opt_ftb",
                "component": "Column",
                "children": ["img_ftb", "btn_ftb"]
            },
            {
                "id": "img_ftb",
                "component": "Image",
                "data": {"url": f"data:image/png;base64,{ftb_icon}"},
                "text": "First-time buyer icon"
            },
            {
                "id": "btn_ftb",
                "component": "Button",
                "text": "First-time buyer"
            },
            # Remortgage
            {
                "id": "opt_remortgage",
                "component": "Column",
                "children": ["img_remortgage", "btn_remortgage"]
            },
            {
                "id": "img_remortgage",
                "component": "Image",
                "data": {"url": f"data:image/png;base64,{remortgage_icon}"},
                "text": "Remortgage icon"
            },
            {
                "id": "btn_remortgage",
                "component": "Button",
                "text": "Remortgage"
            },
            # Buy-to-let
            {
                "id": "opt_btl",
                "component": "Column",
                "children": ["img_btl", "btn_btl"]
            },
            {
                "id": "img_btl",
                "component": "Image",
                "data": {"url": f"data:image/png;base64,{btl_icon}"},
                "text": "Buy-to-let icon"
            },
            {
                "id": "btn_btl",
                "component": "Button",
                "text": "Buy-to-let"
            },
            # Moving home
            {
                "id": "opt_moving",
                "component": "Column",
                "children": ["img_moving", "btn_moving"]
            },
            {
                "id": "img_moving",
                "component": "Image",
                "data": {"url": f"data:image/png;base64,{moving_icon}"},
                "text": "Moving home icon"
            },
            {
                "id": "btn_moving",
                "component": "Button",
                "text": "Moving home"
            }
        ]
        
        payload = {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": "main",
                "components": components
            }
        }
        
    return {"a2ui_payload": payload}

def handle_ui_action(state: AgentState):
    # Action logic is injected by main.py adjusting state before routing here
    return {}

def render_summary_a2ui(state: AgentState):
    ltv = state.get("ltv", 0)
    selection = state.get("selection", {})
    product_id = selection.get("productId")
    products = state.get("products", [])
    selected_prod = next((p for p in products if p["id"] == product_id), None)
    
    components = [
        {
            "id": "root",
            "component": "Column",
            "children": ["summary_header", "summary_card", "disclaimer", "aip_button"]
        },
        {
            "id": "summary_header",
            "component": "Text",
            "text": "Your Agreement in Principle (AiP)",
            "variant": "h2"
        },
        {
            "id": "summary_card",
            "component": "ProductCard",
            "data": selected_prod or (products[0] if products else {})
        },
        {
            "id": "disclaimer",
            "component": "Text",
            "text": "Your home may be repossessed if you do not keep up repayments on your mortgage. Overall cost for comparison: 5.6% APRC Representative.",
            "variant": "body"
        },
        {
            "id": "aip_button",
            "component": "Button",
            "text": "Get an AiP online (10 mins)",
            "data": {"url": "https://www.barclays.co.uk/mortgages/agreement-in-principle/"}
        }
    ]
    
    payload = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }
    return {"a2ui_payload": payload}

def voice_confirm(state: AgentState):
    return {}

def route_ui_action(state: AgentState):
    sel = state.get("selection", {})
    if sel.get("confirmed"):
        return "render_summary_a2ui"
    if sel.get("productId"):
        return "render_summary_a2ui"
    return "call_mortgage_tools"

def router_node(state: AgentState):
    # Determine routing based on the last event type
    if state.get("last_event") == "action":
         return "handle_ui_action"
    return "interpret_intent"

workflow = StateGraph(AgentState)

workflow.add_node("ingest_input", ingest_input)
workflow.add_node("interpret_intent", interpret_intent)
workflow.add_node("call_mortgage_tools", call_mortgage_tools)
workflow.add_node("render_products_a2ui", render_products_a2ui)
workflow.add_node("handle_ui_action", handle_ui_action)
workflow.add_node("render_summary_a2ui", render_summary_a2ui)
workflow.add_node("voice_confirm", voice_confirm)

workflow.add_edge(START, "ingest_input")
workflow.add_conditional_edges("ingest_input", router_node)

workflow.add_edge("interpret_intent", "call_mortgage_tools")
workflow.add_edge("call_mortgage_tools", "render_products_a2ui")
workflow.add_edge("render_products_a2ui", END)

workflow.add_conditional_edges("handle_ui_action", route_ui_action)
workflow.add_edge("render_summary_a2ui", "voice_confirm")
workflow.add_edge("voice_confirm", END)

app_graph = workflow.compile()
