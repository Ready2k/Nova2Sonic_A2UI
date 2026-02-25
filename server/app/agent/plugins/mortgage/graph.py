import os
import re
import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional, TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field
from .tools import calculate_ltv, fetch_mortgage_products, recalculate_monthly_payment
from geopy.geocoders import Nominatim

logger = logging.getLogger(__name__)

# Asset directory: override with ASSETS_DIR env var (set in Docker to /assets).
# Falls back to the repository root resolved relative to this file's location.
_ASSETS_DIR = os.getenv("ASSETS_DIR", str(Path(__file__).resolve().parents[5]))

def append_reducer(a: list, b: list) -> list:
    return a + b


# ─── Helper: UK address validation ────────────────────────────────────────────

def _validate_address_uk(address: str) -> tuple[bool, float | None, float | None]:
    """Geocode a UK address. Returns (found, lat, lng)."""
    try:
        geolocator = Nominatim(user_agent="barclays_mortgage_demo")
        location = geolocator.geocode(address, country_codes="gb")
        if location:
            return True, location.latitude, location.longitude
        return False, None, None
    except Exception as e:
        logger.warning(f"Address validation error: {e}")
        return False, None, None


# ─── Helper: Normalize spoken UK postcode ─────────────────────────────────────

_SPOKEN_DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

_PHONETIC_ALPHABET = {
    "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O",
    "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T",
    "uniform": "U", "victor": "V", "whiskey": "W", "whisky": "W", "xray": "X",
    "yankee": "Y", "zulu": "Z",
    "sugar": "S", "tommy": "T", "mother": "M", "apple": "A", "london": "L",
    "peter": "P", "queen": "Q", "robert": "R", "uncle": "U", "baker": "B",
    "dog": "D", "edward": "E", "freddie": "F", "george": "G", "harry": "H",
    "isaac": "I", "jack": "J", "king": "K", "mary": "M", "nellie": "N",
    "oliver": "O", "simon": "S", "william": "W", "zebra": "Z",
    # Common UK postcode prefixes
    "st": "ST", "sw": "SW", "se": "SE", "nw": "NW", "ne": "NE", "ec": "EC", "wc": "WC",
    "eh": "EH", "ab": "AB", "nr": "NR", "br": "BR", "cr": "CR", "da": "DA", "en": "EN",
    "ha": "HA", "ig": "IG", "kt": "KT", "rm": "RM", "sm": "SM", "tw": "TW", "ub": "UB", "wd": "WD"
}

_IGNORED_WORDS = {"for", "is", "at", "the", "and", "it", "my", "of"}

def _normalize_spoken_to_postcode(text: str) -> str | None:
    """
    Try to extract a UK postcode from STT spoken-letter/phonetic output.
    e.g. "s. for sierra t. for tango three five t. for tango w. for whisky" → "ST3 5TW"
    """
    # Remove dots/hyphens, handle cases like 'tangothree' by splitting digits from letters
    cleaned = re.sub(r'([a-z])([0-9])', r'\1 \2', text.lower())
    cleaned = re.sub(r'([0-9])([a-z])', r'\1 \2', cleaned)
    
    # Handle concatenated spoken digits like "threefive" -> "three five"
    for word in _SPOKEN_DIGITS.keys():
        cleaned = re.sub(rf'({word})([a-z0-9])', r'\1 \2', cleaned)
        cleaned = re.sub(rf'([a-z0-9])({word})', r'\1 \2', cleaned)

    cleaned = re.sub(r'[.\-]', ' ', cleaned).strip()
    
    tokens = cleaned.split()
    parts = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        val = None
        
        if tok in _SPOKEN_DIGITS:
            val = _SPOKEN_DIGITS[tok]
        elif tok in _PHONETIC_ALPHABET:
            val = _PHONETIC_ALPHABET[tok]
        elif len(tok) == 1 and (tok.isalpha() or tok.isdigit()):
            val = tok.upper()
            
        if val:
            # Look ahead to handle "X for [Word]" or "X [Word]" patterns
            # which cause duplication if processed individually.
            skip = 0
            if i + 1 < len(tokens):
                next_tok = tokens[i+1]
                # Pattern: "S for Sugar" or "S for Sierra"
                if next_tok in _IGNORED_WORDS and i + 2 < len(tokens):
                    look_ahead = tokens[i+2]
                    la_val = _PHONETIC_ALPHABET.get(look_ahead) or (look_ahead.upper() if len(look_ahead) == 1 else None)
                    if la_val == val or (len(look_ahead) > 1 and look_ahead.lower().startswith(tok.lower()) and look_ahead not in _SPOKEN_DIGITS):
                        skip = 2
                # Pattern: "S Sierra" or "S Sugar"
                else:
                    la_val = _PHONETIC_ALPHABET.get(next_tok) or (next_tok.upper() if len(next_tok) == 1 else None)
                    if la_val == val or (len(next_tok) > 1 and next_tok.lower().startswith(tok.lower()) and next_tok not in _SPOKEN_DIGITS):
                        skip = 1
            
            parts.append(val)
            i += skip + 1
            continue
            
        i += 1
            
    joined = ''.join(parts)
    # UK postcode regex: outward (1-2 letters + 1-2 digits + optional letter) + inward (1 digit + 2 letters)
    match = re.search(r'([A-Z]{1,2}[0-9]{1,2}[A-Z]?)([0-9][A-Z]{2})', joined)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return None


# ─── Helper: Nearest Barclays branch via Overpass ─────────────────────────────

def _find_nearest_barclays(lat: float, lng: float) -> dict | None:
    """Find the nearest Barclays Bank branch using the OpenStreetMap Overpass API."""
    query = (
        f'[out:json][timeout:10];'
        f'(node["brand"="Barclays"]["amenity"="bank"](around:10000,{lat},{lng});'
        f'way["brand"="Barclays"]["amenity"="bank"](around:10000,{lat},{lng}););'
        f'out center 1;'
    )
    try:
        data = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "barclays_mortgage_demo/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            result = json.loads(resp.read())
        elements = result.get("elements", [])
        if not elements:
            return None
        el = elements[0]
        tags = el.get("tags", {})
        name = tags.get("name", "Barclays Bank")
        addr_parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:city", ""),
            tags.get("addr:postcode", ""),
        ]
        addr_str = " ".join(p for p in addr_parts if p).strip()
        if el.get("type") == "way":
            blat = el["center"]["lat"]
            blng = el["center"]["lon"]
        else:
            blat = el.get("lat", lat)
            blng = el.get("lon", lng)
        return {"name": name, "address": addr_str, "lat": blat, "lng": blng}
    except Exception as e:
        logger.warning(f"Barclays branch lookup error: {e}")
        return None


# ─── Helper: Mortgage FAQ ─────────────────────────────────────────────────────

def _faq_fallback(question: str) -> str:
    """Keyword-based FAQ answers used when the LLM is unavailable."""
    q = question.lower()
    if any(w in q for w in ["document", "paperwork", "proof", "id ", "payslip"]):
        return ("You'll typically need: recent payslips or SA302 (self-employed), three months' bank statements, "
                "photo ID, and proof of address. We'll confirm the full list when your application is underway.")
    if any(w in q for w in ["how long", "timeline", "time does", "how much time", "take long"]):
        return ("A full mortgage application usually takes 2–4 weeks from submission to offer, though it varies. "
                "Your Agreement in Principle is usually available instantly.")
    if any(w in q for w in ["aip", "agreement in principle", "decision in principle", "dip"]):
        return ("An Agreement in Principle (AiP) is a conditional confirmation that Barclays would be willing "
                "to lend you a certain amount, subject to full underwriting. It doesn't affect your credit score "
                "and is valid for 90 days.")
    if any(w in q for w in ["ltv", "loan to value", "loan-to-value"]):
        return ("LTV — Loan-to-Value — is your mortgage as a percentage of the property value. "
                "The lower your LTV, the better the rates available. For example, a 60% LTV gives access "
                "to our most competitive products.")
    if any(w in q for w in ["solicitor", "conveyancer", "legal"]):
        return ("Yes, you'll need a solicitor or licensed conveyancer for the legal work. "
                "Barclays can recommend one, or you're welcome to use your own.")
    if any(w in q for w in ["fee", "cost", "charge", "stamp duty", "how much will"]):
        return ("Key costs include: the mortgage arrangement fee (shown on each product), solicitor fees, "
                "a valuation fee, and Stamp Duty Land Tax if applicable. I'll break these down once you've "
                "chosen a product.")
    if any(w in q for w in ["overpay", "early repayment", "pay off early", "erc"]):
        return ("Most Barclays fixed-rate mortgages allow up to 10% overpayment per year without an "
                "Early Repayment Charge. Full terms are shown on each product card.")
    if any(w in q for w in ["fixed", "variable", "tracker", "difference"]):
        return ("A fixed-rate mortgage locks your interest rate for a set period, so your payments stay "
                "the same. A variable or tracker rate moves with the Bank of England base rate — "
                "potentially cheaper, but less predictable.")
    if any(w in q for w in ["next", "what happens", "after this", "what do i"]):
        return ("Once I have all your details, I'll show you your personalised mortgage products. "
                "You select one, get your AiP, and a Barclays specialist will then guide you through "
                "the full application.")
    if any(w in q for w in ["survey", "valuation", "survey"]):
        return ("Barclays will carry out a basic mortgage valuation of the property. You can also choose "
                "a more detailed HomeBuyer Report or full structural survey for extra peace of mind.")
    if any(w in q for w in ["credit", "credit score", "credit check"]):
        return ("A full mortgage application involves a hard credit check. However, the Agreement in "
                "Principle only uses a soft search, which won't affect your credit score.")
    return ("Great question about the mortgage process — a Barclays specialist would be happy to walk "
            "you through that in detail. I can help find your nearest branch if you'd like to pop in.")


def _answer_process_question(question: str, intent: dict, current_stage: str) -> str:
    """Use Nova Lite to answer a mortgage process question with journey context."""
    if not (os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE")):
        return _faq_fallback(question)
    try:
        from langchain_aws import ChatBedrockConverse
        from langchain_core.messages import HumanMessage, SystemMessage

        model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
        llm = ChatBedrockConverse(model=model_id, region_name=os.getenv("AWS_REGION", "us-east-1"))

        known = {k: v for k, v in intent.items() if v is not None and k not in ("lat", "lng", "notes")}
        system_prompt = (
            "You are a knowledgeable Barclays Mortgage Assistant. "
            "The customer has asked a question about the mortgage process. "
            "Answer it clearly and concisely in 2–3 sentences, using plain English. "
            "Relate your answer to their specific situation where possible.\n\n"
            f"Current journey stage: {current_stage}\n"
            f"What we know about their mortgage: {known}\n\n"
            "Do NOT ask for missing information in this response — just answer the question. "
            "Do NOT start with filler words like 'Great question!' or 'Certainly!'."
        )
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=question)])
        answer = response.content

        refusal_keywords = ["unable to respond", "cannot fulfill", "guardrail", "not allowed", "cannot provide"]
        if any(kw in answer.lower() for kw in refusal_keywords):
            return _faq_fallback(question)
        return answer
    except Exception as e:
        logger.error(f"FAQ LLM error: {e}")
        return _faq_fallback(question)


# ── Domain state accessors ─────────────────────────────────────────────────────
# These provide backward-compatible reads during the Phase 3 migration.
# Once all top-level fields are removed, these can be inlined or deleted.

def _dm(state: dict) -> dict:
    """Return the mortgage domain sub-dict, creating it if absent."""
    domain = state.setdefault("domain", {})
    if "mortgage" not in domain:
        domain["mortgage"] = {}
    return domain["mortgage"]


def _dm_get(state: dict, key: str, default=None):
    """
    Read a field from domain.mortgage with fallback to top-level key.
    Supports the dual-write window where both paths may exist.
    """
    dm = state.get("domain", {}).get("mortgage", {})
    if key in dm:
        return dm[key]
    return state.get(key, default)


def _intent(state: dict) -> dict:
    """
    Return the intent dict, reading from domain.mortgage.intent with
    fallback to top-level state['intent'].
    """
    dm = state.get("domain", {}).get("mortgage", {})
    if "intent" in dm:
        return dm["intent"]
    return state.get("intent", {})


# ─── Agent State ───────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # ── CommonState envelope (shared with all plugins) ───────────────────
    mode: str
    device: str
    transcript: str
    messages: Annotated[List[Dict[str, Any]], append_reducer]
    ui: Dict[str, Any]
    errors: Optional[Dict[str, Any]]
    pendingAction: Optional[Dict[str, Any]]
    outbox: Annotated[List[Dict[str, Any]], append_reducer]
    domain: Dict[str, Any]                 # All mortgage domain data under domain["mortgage"]


# ─── Intent model ─────────────────────────────────────────────────────────────

class MortgageIntent(BaseModel):
    category: Optional[str] = Field(description="The type of mortgage: 'First-time buyer', 'Remortgage', 'Buy-to-let', or 'Moving home'", default=None)
    propertyValue: Optional[int] = Field(description="The value of the property in GBP", default=None)
    loanBalance: Optional[int] = Field(description="The requested mortgage amount (for purchases) or existing loan balance (for remortgages) in GBP", default=None)
    fixYears: Optional[int] = Field(description="The requested fixed rate term in years, e.g. 2, 5, 10", default=None)
    termYears: Optional[int] = Field(description="The overall mortgage repayment term in years, default is 25", default=25)
    existingCustomer: Optional[bool] = Field(description="Whether the user already banks with Barclays", default=None)
    propertySeen: Optional[bool] = Field(description="Whether the user has already found a property they want to buy", default=None)
    isJoint: Optional[bool] = Field(description="Whether this is a joint mortgage application", default=None)
    address: Optional[str] = Field(description="The address of the property", default=None)
    lat: Optional[float] = Field(description="Latitude of the property", default=None)
    lng: Optional[float] = Field(description="Longitude of the property", default=None)
    notes: Optional[str] = Field(description="Any personal life context or feelings the user shared (e.g. 'excited about first home', 'nervous about rates')", default=None)
    annualIncome: Optional[int] = Field(description="The total annual gross income for the application (summed for joint applications) in GBP", default=None)
    processQuestion: Optional[str] = Field(description="If the user is asking a question about the mortgage process (documents needed, timeline, what AiP means, fees, next steps, LTV, solicitors, overpayments, etc.), capture the question verbatim here. Leave null if they are just providing data.", default=None)


# ─── Nodes ────────────────────────────────────────────────────────────────────

def ingest_input(state: AgentState):
    logger.info(f"NODE: ingest_input - transcript='{state.get('transcript')}'")
    return {}


def interpret_intent(state: AgentState):
    transcript = state.get("transcript", "").strip()
    logger.info(f"NODE: interpret_intent - input='{transcript}'")
    intent = _intent(state) or {}
    messages = state.get("messages", [])

    # Carry forward existing validation state
    address_validation_failed = _dm_get(state, "address_validation_failed", False)
    last_attempted_address = _dm_get(state, "last_attempted_address")

    # Determine what question was last asked (so we can interpret short answers like "yes" correctly)
    last_question_context = ""
    if intent.get("existingCustomer") is None:
        last_question_context = "The last question asked was: 'Do you already bank with Barclays?' — so 'yes'/'yes it is'/'yeah' means existingCustomer=true, 'no'/'nope' means existingCustomer=false."
    elif intent.get("propertySeen") is None:
        last_question_context = "The last question asked was: 'Have you found a property yet?' — so 'yes'/'yeah'/'found one' means propertySeen=true, 'no'/'not yet' means propertySeen=false."
    elif intent.get("propertyValue") is None:
        last_question_context = "The last question asked was about property value. Extract the number from the answer."
    elif intent.get("annualIncome") is None:
        last_question_context = "The last question asked was about annual gross income (yearly salary). Extract and SUM the incomes if multiple are provided (e.g. 'mine is 40k and my wife's is 28k' -> 68000)."
    elif intent.get("loanBalance") is None:
        last_question_context = "The last question asked was about the mortgage amount or loan balance. Extract the number from the answer."
    elif intent.get("fixYears") is None:
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

            device = state.get("device", "desktop")
            current_prompt = (
                f"Extract mortgage details from the user's latest response.\n"
                f"Context: {last_question_context}\n"
                f"Device: {device}\n"
                f"Current known intent: {intent}\n"
                f"User just said: '{transcript}'\n\n"
                "Rules:\n"
                "- ONLY extract fields that are explicitly mentioned or clearly answered in the LATEST USER MESSAGE.\n"
                "- IMPORTANT: Do NOT guess, assume, or provide default values for fields like propertyValue, annualIncome, or loanBalance if not stated.\n"
                "- If the user says 'Number One [Street]' or 'First House', do NOT interpret 'one' as fixYears or propertyValue; it is part of the address.\n"
                "- Interpret short answers (yes/no/yeah/nope) using the 'Context' provided above.\n"
                "- For money, '400k' = 400000. If joint application, SUM the incomes provided.\n"
                "- If the user mentions applying with a partner, set 'isJoint' to true.\n"
                "- If they share life feelings (excited, nervous), capture it in 'notes'.\n"
                "- If they are just being conversational ('okay', 'thanks'), leave all fields as they were.\n"
                "- If the user provides a postcode (especially if they spell it out phonetically like 's for sugar'), extract it into the 'address' field. Recognize that 'for' often precedes a phonetic word (e.g., 't for tango' means 'T').\n"
                "- If the user is giving a property address and postcode, combine them into 'address'.\n"
                "- DO NOT change any field that already has a value unless the user is explicitly CORRECTING it.\n"
            )
            lc_messages.append(HumanMessage(content=current_prompt))

            result = structured_llm.invoke(lc_messages)
            idict = result.model_dump(exclude_none=True)

            new_intent = {**intent, **idict}
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Fallback to mock parsing due to Bedrock error: {e}")
            new_intent = dict(intent)
    else:
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

    # ── Address/Postcode Extraction & Validation ─────────────────────────────
    new_address = new_intent.get("address")
    old_address = intent.get("address")
    
    # Try to recover a phonetic postcode from the transcript
    spoken_pc = _normalize_spoken_to_postcode(transcript)
    
    # If we have a spoken postcode, try to use it to enrich or recover the address
    if spoken_pc:
        logger.info(f"Phonetic postcode detected: '{spoken_pc}'")
        # Scenario A: User is giving a new address but also spelling the postcode
        if new_address and spoken_pc.replace(" ","").lower() not in new_address.replace(" ","").lower():
            new_address = f"{new_address}, {spoken_pc}"
            new_intent["address"] = new_address
        # Scenario B: Address validation failed previously, user just said the postcode
        elif not new_address and address_validation_failed and last_attempted_address:
            new_address = f"{last_attempted_address}, {spoken_pc}"
            new_intent["address"] = new_address

    # Validate the current address candidate if it changed
    if new_address and new_address != old_address:
        success, vlat, vlng = _validate_address_uk(new_address)
        if not success and spoken_pc:
            # Maybe the combined address failed, but the postcode alone might work
            success, vlat, vlng = _validate_address_uk(spoken_pc)
            if success:
                new_address = f"{new_address.split(',')[0]}, {spoken_pc}" if ',' in new_address else spoken_pc
                new_intent["address"] = new_address

        if success:
            new_intent["lat"] = vlat
            new_intent["lng"] = vlng
            address_validation_failed = False
            last_attempted_address = None
            logger.info(f"Address validated: '{new_address}' -> ({vlat}, {vlng})")
        else:
            logger.warning(f"Address validation failed: '{new_address}'")
            last_attempted_address = new_address
            new_intent.pop("address", None) # Keep it missing so agent re-asks
            new_intent.pop("lat", None)
            new_intent.pop("lng", None)
            address_validation_failed = True

    # ── Branch request detection ──────────────────────────────────────────────
    branch_keywords = [
        "nearest branch", "local branch", "visit a branch", "barclays near",
        "nearest barclays", "pop in", "pop down",
        "speak to someone in person", "speak to someone", "speak to a person",
        "talk to someone", "talk to a person", "talk to an advisor",
        "visit in person", "find a branch", "where can i go", "go to a branch",
        "go to a barclays", "go into a branch", "walk in", "walk-in", "in-branch",
        "come in", "visit you", "see someone", "meet someone", "in person",
    ]
    branch_requested = any(kw in transcript.lower() for kw in branch_keywords)

    # ── Process question detection ────────────────────────────────────────────
    # Primary signal: LLM populated processQuestion in the structured output
    process_question: Optional[str] = new_intent.pop("processQuestion", None)

    # Fallback keyword detection when LLM isn't available or didn't catch it
    if not process_question:
        question_triggers = [
            "what is ", "what's ", "what are ", "what does ", "what do i ",
            "how does ", "how do ", "how long ", "how much will ",
            "do i need ", "will i need ", "should i ",
            "explain ", "tell me about ", "what happens ",
            "can i ", "am i able ", "is it possible ",
            "difference between ", "what kind of ", "why do ",
        ]
        t_lower = transcript.lower()
        if any(t_lower.startswith(trig) or f" {trig}" in t_lower for trig in question_triggers):
            process_question = transcript

    if process_question:
        logger.info(f"Process question detected: '{process_question}'")

    # ── Trouble counting ──────────────────────────────────────────────────────
    new_trouble_count = _dm_get(state, "trouble_count", 0)

    if not transcript:
        new_trouble_count += 1
    else:
        RESET_KEYS = {"propertyValue", "loanBalance", "fixYears", "existingCustomer", "propertySeen", "address", "category", "termYears", "annualIncome"}

        intent_changed = any(new_intent.get(k) != intent.get(k) for k in RESET_KEYS)

        struggle_keywords = ["struggling", "help", "don't know", "dont know", "not working", "stuck", "human", "specialist", "agent", "person"]
        is_struggling = any(kw in transcript.lower() for kw in struggle_keywords)

        # ── Address correction detection ──────────────────────────────────────
        wrong_address_keywords = ["wrong address", "not that address", "incorrect address", "wrong place", "not that one", "not on the map", "wrong on the map"]
        if any(kw in transcript.lower() for kw in wrong_address_keywords) and intent.get("address"):
            logger.info("User indicated the address is wrong. Clearing address fields.")
            new_intent.pop("address", None)
            new_intent.pop("lat", None)
            new_intent.pop("lng", None)
            address_validation_failed = True
            last_attempted_address = None # Reset so we ask fresh

        # Asking a process question counts as engagement — don't penalise
        if intent_changed or process_question:
            new_trouble_count = max(0, new_trouble_count - 1) if process_question and not intent_changed else 0
        elif is_struggling:
            new_trouble_count += 2
        else:
            new_trouble_count += 1

    show_support = new_trouble_count >= 2
    logger.info(f"Trouble State: count={new_trouble_count}, show_support={show_support}, transcript='{transcript}'")

    _dm(state)["branch_requested"] = branch_requested
    _dm(state)["address_validation_failed"] = address_validation_failed
    _dm(state)["last_attempted_address"] = last_attempted_address
    _dm(state)["trouble_count"] = new_trouble_count
    _dm(state)["show_support"] = show_support
    _dm(state)["existing_customer"] = new_intent.get("existingCustomer")
    _dm(state)["property_seen"] = new_intent.get("propertySeen")
    _dm(state)["process_question"] = process_question
    _dm(state)["last_question_context"] = last_question_context
    _dm(state)["intent"] = new_intent

    # ── Lost Card detection (Handoff trigger) ──────────────────────────────────
    lost_card_keywords = ["lost card", "stolen card", "freeze my card", "lost my card", "stolen my card"]
    if any(kw in transcript.lower() for kw in lost_card_keywords):
        logger.info("Lost card intent detected in Mortgage agent. Preparing handoff.")
        _dm(state)["handoff_requested"] = "lost_card"

    return {
        "domain": state.get("domain", {}),
    }


def render_missing_inputs(state: AgentState):
    intent = _intent(state)
    missing = []

    category = intent.get("category")

    if not category:
        missing.append("category")
    elif intent.get("existingCustomer") is None:
        missing.append("whether you already bank with Barclays")

    # Conditional logic: Remortgages don't need to 'find' a property
    elif category == "Remortgage":
        if not intent.get("address"):
            missing.append("address")
        elif intent.get("propertyValue") is None:
            missing.append("property value")
        elif intent.get("annualIncome") is None:
            missing.append("annual income")
        elif intent.get("loanBalance") is None:
            missing.append("loan balance")
        elif intent.get("fixYears") is None:
            missing.append("fixed term (years)")

    # Purchase-style flows (FTB, Moving Home, BTL)
    else:
        if intent.get("propertySeen") is None:
            missing.append("if you have already found a property")
        elif intent.get("propertySeen") and not intent.get("address"):
            missing.append("address")
        elif intent.get("propertyValue") is None:
            missing.append("property value")
        elif intent.get("annualIncome") is None:
            missing.append("annual income")
        elif intent.get("loanBalance") is None:
            missing.append("mortgage amount")
        elif intent.get("fixYears") is None:
            missing.append("fixed term (years)")

    new_outbox = []
    new_messages = []

    # ── Answer any process question first ────────────────────────────────────
    faq_answer_text = None
    faq_question_text = _dm_get(state, "process_question")
    if faq_question_text:
        ui_stage = state.get("ui", {}).get("state", "data collection")
        faq_answer_text = _answer_process_question(faq_question_text, intent, ui_stage)
        logger.info(f"Answering process question: '{faq_question_text}' -> '{faq_answer_text[:80]}...'")
        new_outbox.append({"type": "server.voice.say", "payload": {"text": faq_answer_text}})
        new_messages.append({"role": "assistant", "text": faq_answer_text})

    category_val = _intent(state).get("category")
    category_label = category_val if category_val else "a mortgage"
    just_selected = state.get("pendingAction", {}) and state.get("pendingAction", {}).get("data", {}).get("action") == "select_category"

    if missing:
        logger.info(f"NODE: render_missing_inputs - missing={missing}, transcript='{state.get('transcript')}'")

        target_field = missing[0]

        # Build extra context for the LLM when an address attempt failed
        address_failure_note = ""
        if target_field == "address" and _dm_get(state, "address_validation_failed", False):
            last_addr = _dm_get(state, "last_attempted_address", "the address you gave")
            address_failure_note = (
                f"IMPORTANT: The user previously gave the address '{last_addr}' but we could not "
                f"verify it against UK records. Politely explain this and ask them to try their "
                f"property postcode instead so we can locate it accurately."
            )

        # Intelligent generation via Nova Lite
        if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
            try:
                from langchain_aws import ChatBedrockConverse
                from langchain_core.messages import HumanMessage, SystemMessage

                model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
                llm = ChatBedrockConverse(model=model_id, region_name=os.getenv("AWS_REGION", "us-east-1"))

                system_prompt = (
                    "You are a professional Barclays Mortgage Assistant. Your goal is to collect "
                    "required information efficiently while remaining polite and helpful. \n\n"
                    "Your personality:\n"
                    "- Direct, professional, and helpful.\n"
                    "- Acknowledge the user's input with specific context, but don't be overly emotional.\n"
                    "- IMPORTANT: If the user is protesting that something is incorrect (e.g., 'that's the wrong address', 'that's not right'), PRIORITIZE addressing their concern and asking for the correct value over moving to the next field.\n"
                    "- Terminology: For First-Time Buyers or home movers, avoid the term 'loan balance' — use 'mortgage amount' or 'amount you wish to borrow' instead.\n"
                    f"Current Product Flow: {category_label}\n"
                    "Goal: Collect the specific detail requested."
                )

                messages = state.get("messages", [])
                notes = intent.get("notes", "No personal context shared yet.")
                device = state.get("device", "desktop")
                user_msg = (
                    f"NOTES ON USER: {notes}\n"
                    f"HISTORY: {messages[-4:]}\n"
                    f"USER JUST SAID: '{state.get('transcript')}'\n"
                    f"DEVICE: {device}\n"
                    f"FIELD NEEDED: {target_field}\n"
                    f"JUST SELECTED CATEGORY: {just_selected} (If true, acknowledge the selection of {category_label} warmly in your opening)\n"
                    f"{address_failure_note}\n\n"
                    "INSTRUCTIONS:\n"
                    "1. Provide a brief, clear answer to any technical questions.\n"
                    "2. Acknowledge what the user just said (or the category they just clicked) by incorporating it into your next question or a brief statement.\n"
                    "3. Ask for the 'field needed' clearly and directly.\n"
                    "4. Keep the total response to 2-3 sentences."
                )

                response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
                msg = response.content

                # Safety Refusal Check
                refusal_keywords = [
                    "unable to respond", "cannot fulfill", "cannot answer",
                    "personal or people", "violate", "policy", "safety",
                    "guardrail", "not allowed", "cannot provide", "restricted"
                ]
                transcript = state.get('transcript', '').lower()
                is_refusal = any(kw in msg.lower() for kw in refusal_keywords) or \
                             any(kw in transcript for kw in refusal_keywords)

                if is_refusal:
                    if any(kw in transcript for kw in refusal_keywords):
                        logger.warning(f"Guardrail/Refusal detected in TRANSCRIPT: {transcript}")
                    else:
                        logger.warning(f"Bedrock refusal detected in RESPONSE: {msg}")
                    if address_failure_note:
                        msg = f"I wasn't able to find that address in our records. Could you share the property's postcode so I can locate it?"
                    else:
                        msg = f"I'm here to help with your Barclays mortgage. To move forward, could you please tell me your {target_field}?"

            except Exception as e:
                logger.error(f"LLM generation error: {e}")
                if address_failure_note:
                    msg = f"I couldn't locate that address — could you provide the postcode instead?"
                else:
                    msg = f"Can you tell me your {target_field}?"
        else:
            if address_failure_note:
                msg = f"I couldn't locate that address — could you provide the property postcode instead?"
            else:
                msg = f"Can you tell me your {target_field}?"

        new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
        new_messages.append({"role": "assistant", "text": msg})
        
        # Store context for STT biasing
        ctx = f"The user is being asked about their {target_field}. " + \
              ("They might be saying 'yes', 'no', or providing a value." if target_field in ["existingCustomer", "propertySeen"] else "")
        _dm(state)["last_question_context"] = ctx
        logger.info(f"Setting STT context: {ctx}")

    # ── Branch request handling ───────────────────────────────────────────────
    branch_outbox_items = []
    branch_components = []
    if _dm_get(state, "branch_requested", False):
        lat = intent.get("lat")
        lng = intent.get("lng")
        if lat and lng:
            branch = _find_nearest_barclays(lat, lng)
            if branch:
                branch_msg = (
                    f"Your nearest Barclays branch is {branch['name']}"
                    + (f" at {branch['address']}" if branch.get("address") else "")
                    + ". I've marked it on screen for you."
                )
                branch_outbox_items.append({"type": "server.voice.say", "payload": {"text": branch_msg}})
                branch_components = [
                    {
                        "id": "branch_header",
                        "component": "Text",
                        "text": "Nearest Barclays Branch",
                        "variant": "h3",
                    },
                    {
                        "id": "branch_card",
                        "component": "DataCard",
                        "data": {
                            "items": [
                                {"label": "Branch", "value": branch["name"]},
                                {"label": "Address", "value": branch["address"] or "See map"},
                            ]
                        },
                    },
                    {
                        "id": "branch_map",
                        "component": "Map",
                        "text": branch["name"],
                        "data": {"address": branch["address"], "lat": branch["lat"], "lng": branch["lng"]},
                    },
                ]
            else:
                branch_outbox_items.append({
                    "type": "server.voice.say",
                    "payload": {"text": "I couldn't find a Barclays branch in our database nearby — visit barclays.co.uk/branch-finder to locate your nearest one."},
                })
        else:
            branch_outbox_items.append({
                "type": "server.voice.say",
                "payload": {"text": "Once you share your property address, I can find your nearest Barclays branch!"},
            })

    intent = _intent(state)
    category = intent.get("category")

    if not category:
        try:
            with open(os.path.join(_ASSETS_DIR, "ftb_b64.txt"), "r") as f: ftb_icon = f.read().strip()
            with open(os.path.join(_ASSETS_DIR, "remortgage_b64.txt"), "r") as f: remortgage_icon = f.read().strip()
            with open(os.path.join(_ASSETS_DIR, "btl_b64.txt"), "r") as f: btl_icon = f.read().strip()
            with open(os.path.join(_ASSETS_DIR, "moving_b64.txt"), "r") as f: moving_icon = f.read().strip()
            # Default fallback for lost card if no b64 file exists
            try:
                with open(os.path.join(_ASSETS_DIR, "lost_card_b64.txt"), "r") as f: lost_card_icon = f.read().strip()
            except:
                lost_card_icon = "" 
        except:
            ftb_icon = remortgage_icon = btl_icon = moving_icon = lost_card_icon = ""

        device = state.get("device", "desktop")
        
        if device == "mobile":
            components = [
                {"id": "root", "component": "Column", "children": ["header", "options_list"]},
                {"id": "header", "component": "Text", "text": "Barclays Services", "variant": "h2"},
                {"id": "options_list", "component": "Column", "children": ["opt_ftb", "opt_remortgage", "opt_btl", "opt_moving", "guidance"]},
                
                {
                    "id": "opt_ftb", 
                    "component": "ListItem", 
                    "text": "First-time Buyer", 
                    "data": {
                        "number": "01",
                        "subtext": "Starting your journey",
                        "rightText": "GUIDE",
                        "url": f"data:image/png;base64,{ftb_icon}",
                        "action": "select_category", 
                        "category": "First-time buyer"
                    }
                },
                {
                    "id": "opt_remortgage", 
                    "component": "ListItem", 
                    "text": "Remortgage", 
                    "data": {
                        "number": "02",
                        "subtext": "Switching your deal",
                        "rightText": "SWITCH",
                        "url": f"data:image/png;base64,{remortgage_icon}",
                        "action": "select_category", 
                        "category": "Remortgage"
                    }
                },
                {
                    "id": "opt_btl", 
                    "component": "ListItem", 
                    "text": "Buy-to-let", 
                    "data": {
                        "number": "03",
                        "subtext": "Investment property",
                        "rightText": "INVEST",
                        "url": f"data:image/png;base64,{btl_icon}",
                        "action": "select_category", 
                        "category": "Buy-to-let"
                    }
                },
                {
                    "id": "opt_moving", 
                    "component": "ListItem", 
                    "text": "Moving Home", 
                    "data": {
                        "number": "04",
                        "subtext": "New house, new mortgage",
                        "rightText": "RELOCATE",
                        "url": f"data:image/png;base64,{moving_icon}",
                        "action": "select_category", 
                        "category": "Moving home"
                    }
                },
                {
                    "id": "guidance",
                    "component": "Text",
                    "text": "Select a mortgage type to get started",
                    "variant": "caption"
                }
            ]
        else:
            components = [
                {"id": "root", "component": "Column", "children": ["header", "options_grid"]},
                {"id": "header", "component": "Text", "text": "How can we help today?", "variant": "h2"},
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
        new_outbox.extend(branch_outbox_items)
        ui_state = dict(state.get("ui", {}))
        ui_state["state"] = "LOADING"
        _dm(state)["branch_requested"] = False
        _dm(state)["process_question"] = None
        return {"outbox": new_outbox, "ui": ui_state, "messages": new_messages, "transcript": "", "domain": state.get("domain", {})}

    pv = intent.get("propertyValue")
    lb = intent.get("loanBalance")
    fy = intent.get("fixYears")
    addr = intent.get("address")
    income = intent.get("annualIncome")

    pv_text = f"£{pv:,}" if pv else "Pending..."
    lb_text = f"£{lb:,}" if lb else "Pending..."
    fy_text = f"{fy} Years" if fy else "Pending..."
    addr_text = addr if addr else "N/A"
    income_text = f"£{income:,}/yr" if income else "Pending..."

    # Determine which field the agent is currently asking about (first missing one)
    next_missing = missing[0] if missing else None

    # Determine if we should show the details list yet
    show_details = intent.get("existingCustomer") is not None
    is_customer = intent.get("existingCustomer") is True

    category_label = f"[{category}]" if category else ""

    pv_focus = next_missing in ("property value",)
    lb_focus = next_missing in ("loan balance", "mortgage amount")
    fy_focus = next_missing in ("fixed term (years)",)
    addr_focus = next_missing in ("address",)
    income_focus = next_missing in ("annual income",)

    if not show_details:
        components = [
            {"id": "root", "component": "Column", "children": ["journey", "header", "guidance"]},
            {"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 1}},
            {"id": "header", "component": "Text", "text": f"Let’s build your quote {category_label}", "variant": "h2"},
            {"id": "guidance", "component": "Text", "text": "Please confirm if you already bank with us so we can personalize your journey.", "variant": "caption"}
        ]
    else:
        # Re-using icons or using placeholders for now
        # Ideally we'd have specific icons for these
        try:
            with open(os.path.join(_ASSETS_DIR, "ftb_b64.txt"), "r") as f: icon_b64 = f.read().strip()
        except:
            icon_b64 = ""

        components = [
            {"id": "root", "component": "Column", "children": ["journey", "header", "details_col"]},
            {"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 1}},
            {"id": "header", "component": "Text", "text": f"Mortgage Details {category_label}", "variant": "h2"},
            {"id": "details_col", "component": "Column", "children": ["item_addr", "item_pv", "item_income", "item_lb", "item_fy"]},
            
            {
                "id": "item_addr", 
                "component": "ListItem", 
                "text": addr_text or "Pending...", 
                "focus": addr_focus,
                "data": {
                    "subtext": "Property Address",
                    "rightText": "📍",
                    "url": f"data:image/png;base64,{icon_b64}" if addr_text else None
                }
            },
            {
                "id": "item_pv", 
                "component": "ListItem", 
                "text": pv_text or "Pending...", 
                "focus": pv_focus,
                "data": {
                    "subtext": "Property Value",
                    "rightText": "🏠",
                    "url": f"data:image/png;base64,{icon_b64}" if pv_text else None
                }
            },
            {
                "id": "item_income", 
                "component": "ListItem", 
                "text": income_text or "Pending...", 
                "focus": income_focus,
                "data": {
                    "subtext": "Annual Income" + (" (Joint)" if intent.get("isJoint") else ""),
                    "rightText": "💰",
                    "url": f"data:image/png;base64,{icon_b64}" if income_text else None
                }
            },
            {
                "id": "item_lb", 
                "component": "ListItem", 
                "text": lb_text or "Pending...", 
                "focus": lb_focus,
                "data": {
                    "subtext": "Loan Balance" if category == "Remortgage" else "Mortgage Amount",
                    "rightText": "🏦",
                    "url": f"data:image/png;base64,{icon_b64}" if lb_text else None
                }
            },
            {
                "id": "item_fy", 
                "component": "ListItem", 
                "text": fy_text or "Pending...", 
                "focus": fy_focus,
                "data": {
                    "subtext": "Fixed Term",
                    "rightText": "⏳",
                    "url": f"data:image/png;base64,{icon_b64}" if fy_text else None
                }
            }
        ]

    lat = intent.get("lat")
    lng = intent.get("lng")

    # Geocode if address is set but coords are missing (e.g. restored from state without lat/lng)
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

        # Property map
        map_data = {"address": addr}
        if lat and lng:
            map_data.update({"lat": lat, "lng": lng})

        components.insert(3, {"id": "map_view", "component": "Map", "text": addr, "data": map_data})
        components[0]["children"].insert(3, "map_view")

    # Append any branch components
    if branch_components:
        for bc in branch_components:
            components.append(bc)
            components[0]["children"].append(bc["id"])

    # Append FAQ InfoCard if a process question was answered this turn
    if faq_answer_text and faq_question_text:
        components.append({
            "id": "faq_card",
            "component": "InfoCard",
            "text": faq_question_text,
            "data": {"question": faq_question_text, "answer": faq_answer_text},
        })
        components[0]["children"].append("faq_card")

    payload = {
        "version": "v0.9",
        "isExistingCustomer": is_customer,
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }

    new_outbox.append({"type": "server.a2ui.patch", "payload": payload})
    new_outbox.extend(branch_outbox_items)

    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "LOADING"

    _dm(state)["branch_requested"] = False
    _dm(state)["process_question"] = None
    _dm(state)["intent"] = intent
    return {
        "outbox": new_outbox,
        "ui": ui_state,
        "messages": new_messages,
        "transcript": "",
        "domain": state.get("domain", {}),
    }


def call_mortgage_tools(state: AgentState):
    intent = _intent(state)
    pv = intent.get("propertyValue")
    lb = intent.get("loanBalance")
    fy = intent.get("fixYears")

    if pv is None or lb is None:
        _dm(state)["ltv"] = 0.0
        _dm(state)["products"] = []
        return {"domain": state.get("domain", {})}

    ltv = calculate_ltv(pv, lb)
    products = fetch_mortgage_products(ltv, fy or 5)

    ty = intent.get("termYears", 25)
    for p in products:
        calc = recalculate_monthly_payment(lb, p["rate"], ty, p["fee"])
        p.update(calc)

    _dm(state)["ltv"] = ltv
    _dm(state)["products"] = products
    return {"domain": state.get("domain", {})}


def render_products_a2ui(state: AgentState):
    ltv = _dm_get(state, "ltv", 0)
    products = _dm_get(state, "products", [])
    intent = _intent(state)
    new_outbox = []
    new_messages = []

    # ── Answer any process question first ────────────────────────────────────
    products_faq_answer = None
    products_faq_question = _dm_get(state, "process_question")
    if products_faq_question:
        products_faq_answer = _answer_process_question(products_faq_question, intent, "product comparison")
        logger.info(f"Answering process question (products): '{products_faq_question}' -> '{products_faq_answer[:80]}...'")
        new_outbox.append({"type": "server.voice.say", "payload": {"text": products_faq_answer}})
        new_messages.append({"role": "assistant", "text": products_faq_answer})

    ty = intent.get("termYears", 25)
    annual_income = intent.get("annualIncome")
    loan_balance = intent.get("loanBalance")

    components = [
        {"id": "root", "component": "Column", "children": ["journey", "header_text"]}
    ]
    components.append({"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 2}})
    components.append({"id": "header_text", "component": "Text", "text": "Your Comparative Analysis", "variant": "h2"})

    if ltv > 0:
        components[0]["children"].append("ltv_gauge")
        components.append({"id": "ltv_gauge", "component": "Gauge", "value": ltv, "max": 100})

    # ── Affordability progress bar ────────────────────────────────────────────
    if annual_income and loan_balance:
        max_affordable = int(annual_income * 4.5)
        components[0]["children"].append("affordability_bar")
        components.append({
            "id": "affordability_bar",
            "component": "ProgressBar",
            "text": "Borrowing Capacity",
            "data": {
                "value": loan_balance,
                "max": max_affordable,
                "label": f"Borrowing Capacity (max ~\u00a3{max_affordable:,} based on income)",
            },
        })
        if loan_balance > max_affordable:
            components[0]["children"].append("affordability_warning")
            components.append({
                "id": "affordability_warning",
                "component": "BenefitCard",
                "variant": "Warning",
                "text": "Affordability Notice",
                "data": {
                    "detail": (
                        f"Based on your income of \u00a3{annual_income:,}/yr, "
                        f"the standard affordability limit is approximately \u00a3{max_affordable:,} "
                        f"(4.5\u00d7 income). Your requested loan of \u00a3{loan_balance:,} exceeds this. "
                        f"A mortgage specialist will review your full financial profile."
                    )
                },
            })

    if products:
        # Hero stat: best (lowest) monthly payment
        best_monthly = min(p.get("monthlyPayment", 9999) for p in products)
        components[0]["children"].append("monthly_stat")
        components.append({
            "id": "monthly_stat",
            "component": "StatCard",
            "data": {
                "value": f"\u00a3{best_monthly:,.0f}",
                "label": "Best Monthly Payment",
                "sub": f"Over {ty} years \u2014 adjust the term below",
            },
        })

        components[0]["children"].append("market_insight")
        components.append({"id": "market_insight", "component": "ComparisonBadge", "text": "Market Leading: These rates are in the top 5% for your LTV tier"})

        components[0]["children"].append("products_row")
        components.append({"id": "products_row", "component": "Row", "children": [f"prod_{i}" for i in range(len(products))]})
        for i, p in enumerate(products):
            components.append({"id": f"prod_{i}", "component": "ProductCard", "data": p})

        # Payment breakdown
        breakdown = [
            {"label": "Capital Repayment", "value": f"\u00a3{int((products[0].get('monthlyPayment', 0)) * 0.4):,} (Est.)"},
            {"label": "Interest Portion", "value": f"\u00a3{int((products[0].get('monthlyPayment', 0)) * 0.6):,} (Est.)"}
        ]
        components[0]["children"].append("pmt_breakdown")
        components.append({"id": "pmt_breakdown", "component": "DataCard", "data": {"items": breakdown}})

        # ── Term slider — lets user drag to recalculate in real time ─────────
        components[0]["children"].append("term_slider")
        components.append({
            "id": "term_slider",
            "component": "Slider",
            "text": "Repayment Term",
            "data": {"min": 5, "max": 35, "value": ty, "step": 1, "unit": " yrs", "label": "Repayment Term"},
        })

    # ── Branch request handling ───────────────────────────────────────────────
    if _dm_get(state, "branch_requested", False):
        lat = intent.get("lat")
        lng = intent.get("lng")
        if lat and lng:
            branch = _find_nearest_barclays(lat, lng)
            if branch:
                branch_msg = (
                    f"Your nearest Barclays branch is {branch['name']}"
                    + (f" at {branch['address']}" if branch.get("address") else "")
                    + ". I've marked it on the screen."
                )
                new_outbox.append({"type": "server.voice.say", "payload": {"text": branch_msg}})
                components[0]["children"].extend(["branch_header", "branch_card", "branch_map"])
                components.extend([
                    {"id": "branch_header", "component": "Text", "text": "Nearest Barclays Branch", "variant": "h3"},
                    {
                        "id": "branch_card",
                        "component": "DataCard",
                        "data": {
                            "items": [
                                {"label": "Branch", "value": branch["name"]},
                                {"label": "Address", "value": branch["address"] or "See map"},
                            ]
                        },
                    },
                    {
                        "id": "branch_map",
                        "component": "Map",
                        "text": branch["name"],
                        "data": {"address": branch["address"], "lat": branch["lat"], "lng": branch["lng"]},
                    },
                ])
            else:
                new_outbox.append({
                    "type": "server.voice.say",
                    "payload": {"text": "I couldn't find a nearby Barclays branch in our database — visit barclays.co.uk/branch-finder for your local branch."},
                })
        else:
            new_outbox.append({
                "type": "server.voice.say",
                "payload": {"text": "Once you share your property address, I can find your nearest Barclays branch!"},
            })

    # FAQ InfoCard persisted in UI
    if products_faq_answer and products_faq_question:
        components.append({
            "id": "faq_card",
            "component": "InfoCard",
            "text": products_faq_question,
            "data": {"question": products_faq_question, "answer": products_faq_answer},
        })
        components[0]["children"].append("faq_card")

    payload = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "main",
            "components": components
        }
    }

    new_outbox.append({"type": "server.a2ui.patch", "payload": payload})

    msg = ""
    if os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
        try:
            from langchain_aws import ChatBedrockConverse
            from langchain_core.messages import HumanMessage, SystemMessage

            model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
            llm = ChatBedrockConverse(model=model_id, region_name=os.getenv("AWS_REGION", "us-east-1"))

            system_prompt = (
                "You are a professional Barclays Mortgage Assistant. The user has provided their details, "
                "and you have found mortgage products for them. Briefly introduce the options shown "
                "on screen in 1-2 sentences.\n\n"
                "RULES:\n"
                "- If the user's requested fixed term (fixYears) doesn't exactly match the products found, acknowledge this and explain these are the closest matches available.\n"
                "- Mention the LTV and the key benefit of the top product.\n"
                "- Stay professional and helpful."
            )

            user_msg = f"User Intent: {_intent(state)}\n"
            user_msg += f"Calculated LTV: {ltv}%\n"
            user_msg += f"Requested Fix Years: {_intent(state).get('fixYears')}\n"
            user_msg += f"Found Products: {[{'name': p['name'], 'years': p.get('years'), 'rate': p['rate']} for p in products]}\n"

            response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
            msg = response.content

            refusal_keywords = [
                "unable to respond", "cannot fulfill", "cannot answer",
                "personal or people", "violate", "policy", "safety",
                "guardrail", "not allowed", "cannot provide", "restricted"
            ]
            if any(kw in msg.lower() for kw in refusal_keywords):
                logger.warning(f"Bedrock refusal detected in product intro: {msg}")
                msg = f"Based on the details provided, I've found some mortgage options for you. Take a look at the products below."

        except Exception as e:
            logger.error(f"LLM product intro generation error: {e}")
            msg = f"Based on a {ltv}% LTV, I've found some {_intent(state).get('fixYears', 5)}-year options for you."
    else:
        msg = f"Based on a {ltv}% LTV, I've found some {_intent(state).get('fixYears', 5)}-year options for you."

    if state.get("ui", {}).get("state") != "COMPARISON":
        new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
        new_messages.append({"role": "assistant", "text": msg})

    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "COMPARISON"

    _dm(state)["branch_requested"] = False
    _dm(state)["process_question"] = None
    return {
        "outbox": new_outbox,
        "ui": ui_state,
        "messages": new_messages,
        "transcript": "",
        "domain": state.get("domain", {}),
    }


def recalculate_and_patch(state: AgentState):
    intent = _intent(state)
    lb = intent.get("loanBalance")
    products = _dm_get(state, "products", [])
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

    _dm(state)["products"] = products
    return {"outbox": new_outbox, "ui": ui_state, "domain": state.get("domain", {}), "transcript": ""}


def handle_ui_action(state: AgentState):
    action = state.get("pendingAction")
    if not action:
        return {}

    action_id = action.get("id")
    data = action.get("data", {})
    if isinstance(data, dict) and data.get("action"):
        action_id = data.get("action")

    intent = dict(_intent(state))
    selection = dict(_dm_get(state, "selection", {}))

    if action_id == "reset_flow":
        _dm(state)["address_validation_failed"] = False
        _dm(state)["last_attempted_address"] = None
        _dm(state)["existing_customer"] = None
        _dm(state)["property_seen"] = None
        _dm(state)["intent"] = {"propertyValue": None, "loanBalance": None, "fixYears": None, "termYears": 25, "category": None, "annualIncome": None}
        _dm(state)["selection"] = {}
        _dm(state)["products"] = []
        _dm(state)["ltv"] = 0.0
        return {
            "errors": None,
            "transcript": "",
            "domain": state.get("domain", {}),
        }
    elif action_id == "select_category":
        category = data.get("category")
        intent["category"] = category
        _dm(state)["intent"] = intent
        return {"domain": state.get("domain", {})}
    elif action_id == "update_term":
        intent["termYears"] = data.get("termYears", intent.get("termYears", 25))
        selection["termYears"] = intent["termYears"]
        _dm(state)["intent"] = intent
        _dm(state)["selection"] = selection
        return {"domain": state.get("domain", {})}
    elif action_id == "select_product":
        selection["productId"] = data.get("productId")
        _dm(state)["selection"] = selection
        return {"domain": state.get("domain", {})}
    elif action_id == "confirm_application":
        selection["confirmed"] = True
        _dm(state)["selection"] = selection
        return {"domain": state.get("domain", {})}

    return {}


def clear_pending_action(state: AgentState):
    return {"pendingAction": None}


def render_summary_a2ui(state: AgentState):
    selection = _dm_get(state, "selection", {})
    product_id = selection.get("productId")
    products = _dm_get(state, "products", [])
    selected_prod = next((p for p in products if p["id"] == product_id), None)
    chosen = selected_prod or (products[0] if products else {})
    new_outbox = []
    new_messages = []

    monthly = chosen.get("monthlyPayment", 0)
    ty = _intent(state).get("termYears", 25)

    components = [
        {"id": "root", "component": "Column", "children": [
            "journey", "summary_header", "monthly_hero", "summary_card",
            "docs_checklist", "disclaimer", "aip_button"
        ]},
        {"id": "journey", "component": "Timeline", "data": {"steps": ["Intent", "Property", "Quotes", "Summary"], "current": 3}},
        {"id": "summary_header", "component": "Text", "text": "Your Agreement in Principle (AiP)", "variant": "h2"},
        {
            "id": "monthly_hero",
            "component": "StatCard",
            "data": {
                "value": f"\u00a3{monthly:,.0f}",
                "label": "Monthly Repayment",
                "sub": f"Fixed for {chosen.get('rate', '')}% over {ty} years",
                "trend": "Rate locked — no surprises",
                "trendUp": True,
            },
        },
        {"id": "summary_card", "component": "ProductCard", "data": chosen},
        {
            "id": "docs_checklist",
            "component": "Checklist",
            "text": "Documents You\u2019ll Need",
            "data": {
                "items": [
                    {"label": "Photo ID", "note": "Passport or UK driving licence", "checked": False},
                    {"label": "Proof of address", "note": "Utility bill or bank statement (last 3 months)", "checked": False},
                    {"label": "Last 3 months\u2019 payslips", "note": "Or SA302 if self-employed", "checked": False},
                    {"label": "Last 3 months\u2019 bank statements", "note": "Main current account", "checked": False},
                    {"label": "P60 (most recent)", "note": "Or last 2 years\u2019 accounts if self-employed", "checked": False},
                ]
            },
        },
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

    msg = "I've prepared your summary. You can see your monthly repayment and the documents you'll need on screen — confirm when you're ready."
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
    msg = "Thank you. Your application has been started successfully, and a specialist will be in touch to discuss the next steps."
    new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
    new_messages.append({"role": "assistant", "text": msg})

    ui_state = dict(state.get("ui", {}))
    ui_state["state"] = "CONFIRMED"
    return {"outbox": new_outbox, "ui": ui_state, "messages": new_messages, "transcript": ""}


def handoff_to_lost_card(state: AgentState):
    """Transition the session to the Lost Card plugin."""
    new_outbox = [
        {
            "type": "server.internal.handoff",
            "payload": {"agent_id": "lost_card"}
        }
    ]
    # Small bridging message
    msg = "I'll hand you over to our card services team right away to help you with that."
    new_outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
    
    return {"outbox": new_outbox, "transcript": "", "pendingAction": None}


# ─── Routers ──────────────────────────────────────────────────────────────────

def _all_required_fields_present(intent: dict) -> bool:
    """True when all fields needed to call mortgage tools are known."""
    category = intent.get("category")
    if not category:
        return False
    
    # Core numeric fields required for the calculator
    base_calc_ready = (
        intent.get("propertyValue") is not None
        and intent.get("annualIncome") is not None
        and intent.get("loanBalance") is not None
        and intent.get("fixYears") is not None
    )
    if not base_calc_ready:
        return False

    # Journey context fields
    if intent.get("existingCustomer") is None:
        return False
    
    if category != "Remortgage":
        if intent.get("propertySeen") is None:
            return False
        # If they've seen a property, we really should have the address before showing specific product quotes
        if intent.get("propertySeen") and not intent.get("address"):
            return False
            
    return True


def root_router(state: AgentState):
    if state.get("pendingAction"):
        return "handle_ui_action"

    intent = _intent(state)
    if not _all_required_fields_present(intent):
        return "render_missing_inputs"

    return "call_mortgage_tools"


def ui_action_router(state: AgentState):
    action = state.get("pendingAction", {})
    if not action:
        return END
    action_id = action.get("id")
    data = action.get("data", {})
    if isinstance(data, dict) and data.get("action"):
        action_id = data.get("action")
    if action_id == "update_term":
        return "recalculate_and_patch"
    elif action_id == "select_product":
        return "render_summary_a2ui"
    elif action_id == "confirm_application":
        return "confirm_application"
    elif action_id == "lost_card.start":
        return "handoff_to_lost_card"
    elif action_id in ("reset_flow", "select_category"):
        return "render_missing_inputs"
    return "clear_pending_action"


def start_router(state: AgentState):
    if state.get("pendingAction"):
        return "handle_ui_action"
    if state.get("transcript"):
        return "interpret_intent"

    intent = _intent(state)
    if not intent.get("category") or not _all_required_fields_present(intent):
        return "render_missing_inputs"
    return "call_mortgage_tools"


def intent_router(state: AgentState):
    if _dm_get(state, "handoff_requested") == "lost_card":
        return "handoff_to_lost_card"
    intent = _intent(state)
    if not _all_required_fields_present(intent):
        return "render_missing_inputs"
    return "call_mortgage_tools"


# ─── Graph assembly ───────────────────────────────────────────────────────────

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
workflow.add_node("handoff_to_lost_card", handoff_to_lost_card)
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
workflow.add_edge("handoff_to_lost_card", END)

workflow.add_edge("clear_pending_action", END)

app_graph = workflow.compile()
