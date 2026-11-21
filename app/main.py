# The Incentive Coach mobile demo app.
#
# A thin web front end over the same agent the evals test. It adds no
# business logic of its own:
#   - The agent loop is run_agent from evals/run.py, with the same model,
#     the same five tools and agent/CLAUDE.md as the system prompt.
#   - Every number on screen comes from those tools. The call-list and nudge
#     cards are the tools' own results: the app re-runs each tool call the
#     agent made (same tool, same input; the tools only read the database,
#     so the result is identical) and passes the fields through. Rupee
#     amounts are formatted with rupees() from server/main.py.
#   - Nothing is ever sent. The page offers "Copy" and a WhatsApp share link
#     with the text prefilled; the manager picks the chat and presses send.
#
# How to run it (from the project folder):
#     uv run python app/main.py
# Then open the printed URL on a phone on the same Wi-Fi.
#
# The API key is read from ANTHROPIC_API_KEY in .env and is never printed.

import json
import os
import socket
import sys
import threading
import uuid
from pathlib import Path

import anthropic
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "evals"))   # run.py adds server/ to the path itself

import run as agent  # noqa: E402  (evals/run.py: the agent loop and tools)

SYSTEM_PROMPT = (ROOT / "agent" / "CLAUDE.md").read_text()
STATIC = Path(__file__).parent / "static"

HOST, PORT = "0.0.0.0", 8000
MAX_QUESTIONS = 10       # per browser session
MAX_TOKENS = 8000        # per model call (the evals use 16,000)
MAX_QUESTION_CHARS = 500

load_dotenv(ROOT / ".env")   # puts ANTHROPIC_API_KEY into os.environ, if .env exists
client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
tools = agent.tool_definitions()

# Questions asked so far, per browser session (a cookie holds the session id).
questions_asked = {}
questions_lock = threading.Lock()
SESSION_COOKIE = "ic_session"

app = FastAPI(title="Incentive Coach", docs_url=None, redoc_url=None)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


# ---------------------------------------------------------------------------
# Cards: the tools' own results, passed through for display
# ---------------------------------------------------------------------------

def rerun(call):
    """Re-run one tool call the agent made and return its result as a dict."""
    text, is_error = agent.call_tool(call["name"], call["input"])
    return None if is_error else json.loads(text)


def cards(tool_calls):
    """Build the call-list and nudge cards from the agent's tool calls."""
    call_list, nudges = None, []
    for call in tool_calls:
        if call["name"] == "get_team_status":
            result = rerun(call)
            if not result or "error" in result or "in_reach" not in result["segments"]:
                continue
            call_list = [{
                "rank": row["call_rank"],
                "rep_id": row["rep_id"],
                "name": row["name"],
                "gap": agent.server.rupees(row["gap"]),
                "pace_multiple_needed": row["pace_multiple_needed"],
                "incentive_unlocked": agent.server.rupees(row["incentive_unlocked"]),
                "call_priority": row["call_priority"],
                "flags": [flag.get("note") or flag["type"] for flag in row["flags"]],
            } for row in result["segments"]["in_reach"]]
        elif call["name"] == "fill_nudges":
            result = rerun(call)
            if not result or "error" in result:
                continue
            nudges += [{**message, "segment": result["segment"]} for message in result["messages"]]
    return call_list, nudges


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/session")
def session(request: Request):
    used = questions_asked.get(request.cookies.get(SESSION_COOKIE), 0)
    return {"remaining": MAX_QUESTIONS - used, "limit": MAX_QUESTIONS}


@app.post("/api/ask")
def ask(body: Question, request: Request, response: Response):
    # Defined with plain `def`, so FastAPI runs the slow agent loop in a thread.
    session_id = request.cookies.get(SESSION_COOKIE) or uuid.uuid4().hex
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="strict")

    with questions_lock:
        used = questions_asked.get(session_id, 0)
        if used >= MAX_QUESTIONS:
            return JSONResponse(
                {"error": f"This demo allows {MAX_QUESTIONS} questions per session.", "remaining": 0},
                status_code=429)
        questions_asked[session_id] = used + 1
    remaining = MAX_QUESTIONS - used - 1

    if client is None:
        return JSONResponse({"error": "ANTHROPIC_API_KEY is not set in .env.", "remaining": remaining},
                            status_code=500)
    try:
        result = agent.run_agent(client, tools, SYSTEM_PROMPT, body.question.strip(),
                                 max_tokens=MAX_TOKENS)
    except anthropic.APIError as e:
        # The error type only: never the request, headers or key.
        print(f"API error: {type(e).__name__}", flush=True)
        return JSONResponse({"error": "The model could not be reached. Please try again.",
                             "remaining": remaining}, status_code=502)

    call_list, nudges = cards(result["tool_calls"])
    return {
        "answer": result["answer"],
        "tools_used": list(dict.fromkeys(call["name"] for call in result["tool_calls"])),
        "call_list": call_list,
        "nudges": nudges,
        "truncated": result["stop_reason"] == "max_tokens",
        "remaining": remaining,
    }


# ---------------------------------------------------------------------------
# Start the server
# ---------------------------------------------------------------------------

def local_ip():
    """This machine's address on the local network (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    print("\nIncentive Coach demo (synthetic data, drafts only)")
    print(f"  On this Mac:  http://localhost:{PORT}")
    print(f"  On your phone (same Wi-Fi):  http://{local_ip()}:{PORT}\n", flush=True)
    uvicorn.run(app, host=HOST, port=PORT)
