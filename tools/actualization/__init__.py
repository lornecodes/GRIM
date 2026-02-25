"""
GRIM Actualization Service — LangGraph-powered knowledge ingestion.

Takes any source (repo, conversation, document) and actualizes it
into the Kronos vault as interconnected FDO notes.

Architecture:
    LangGraph StateGraph with 8 nodes:
    intake → extract → search → judge → actualize → validate → crosslink → commit

    Each chunk flows through the graph independently.
    The vault index is live-updated so chunk #50 knows about #1-49.
"""
