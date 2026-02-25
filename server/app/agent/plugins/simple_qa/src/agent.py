"""
Simple Q&A agent — a minimal LangGraph example for testing the A2UI import wizard.

State: messages (List[BaseMessage]) — standard thin-wrapper compatible schema.
The agent echoes the user's question with a stub answer.  Replace the `answer`
node with real logic (LLM call, RAG, tool use, etc.) to build a real agent.
"""

from typing import TypedDict, List

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END, START


class State(TypedDict):
    messages: List[BaseMessage]
    query: str      # optional convenience field — copied from last HumanMessage
    answer: str     # plain-text answer extracted from the last AIMessage


def answer(state: State) -> dict:
    """Produce a response to the most recent human message."""
    last_human = next(
        (m for m in reversed(state.get("messages", [])) if isinstance(m, HumanMessage)),
        None,
    )
    text = last_human.content if last_human else state.get("query", "")
    reply = (
        f"You asked: \"{text}\". "
        "This is the simple_qa test agent — swap this node for real logic."
    )
    return {
        "messages": list(state.get("messages", [])) + [AIMessage(content=reply)],
        "answer": reply,
    }


builder = StateGraph(State)
builder.add_node("answer", answer)
builder.add_edge(START, "answer")
builder.add_edge("answer", END)

graph = builder.compile()
