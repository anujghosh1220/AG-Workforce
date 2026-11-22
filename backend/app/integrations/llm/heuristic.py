"""Deterministic fallback analyst — rule-based intent/state/next-action.

Used when no LLM key is configured. Outputs are honest heuristics, clearly
marked provider='heuristic' so nothing is passed off as AI judgement.
"""
from __future__ import annotations

BUYING = ["price", "pricing", "cost", "demo", "trial", "interested", "sign up",
          "subscription", "plan", "quote", "how much", "book", "meeting", "call"]
OBJECTION = ["expensive", "costly", "not interested", "no thanks", "already have",
             "we use", "competitor", "too costly", "budget"]
OPT_OUT = ["unsubscribe", "opt out", "opt-out", "stop", "remove me", "do not contact",
           "don't contact", "no more emails", "block", "spam"]
HUMAN = ["talk to a human", "real person", "speak to someone", "call me", "complaint"]
QUESTION = ["?", "what", "how", "does", "is ", "can ", "do you", "tell me"]


def _has(text: str, words: list[str]) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def analyze(text: str) -> dict:
    t = text.lower()
    if _has(t, OPT_OUT):
        intent = "opt_out"
    elif _has(t, HUMAN):
        intent = "human_request"
    elif _has(t, BUYING):
        intent = "buying_signal"
    elif _has(t, OBJECTION):
        intent = "objection"
    elif _has(t, QUESTION):
        intent = "question"
    else:
        intent = "other"

    state_map = {
        "opt_out": "opted_out",
        "human_request": "human_handoff",
        "buying_signal": "interested",
        "objection": "engaged",
        "question": "engaged",
        "other": "awaiting_reply",
    }
    summary = f"Prospect message ({intent}): \"{text[:160].strip()}\""
    next_action = {
        "opt_out": "Suppress contact and close conversation",
        "human_request": "Escalate to human owner",
        "buying_signal": "Draft interested-prospect reply and notify owner",
        "objection": "Draft objection-handling reply grounded in product knowledge",
        "question": "Draft answer using approved product knowledge only",
        "other": "Draft brief acknowledgement or schedule follow-up",
    }[intent]
    return {
        "intent": intent,
        "conversation_state": state_map[intent],
        "summary": summary,
        "next_action": next_action,
        "needs_human": intent in ("opt_out", "human_request", "buying_signal"),
        "confidence": "low" if intent == "other" else "medium",
        "provider": "heuristic",
    }


def draft_reply(context: dict) -> dict:
    biz = context.get("business_name", "your cafe")
    city = context.get("city")
    loc = f" in {city}" if city else ""
    body = (
        f"Hi, thanks for getting back to us about {biz}{loc}. "
        "I'm following up on our cafe management & ordering software — "
        "happy to answer any questions or set up a short demo at your convenience. "
        "Would a 15-minute call this week work?"
    )
    return {"subject": f"Re: {biz} — cafe management software", "body": body,
            "provider": "heuristic"}
