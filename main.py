"""
Wine Cellar API
───────────────
POST /analyse-wine      — Vision + web search via Claude; returns structured wine JSON
POST /add-wine          — Forwards wine data to Google Apps Script (Cellar tab)
POST /mark-tasted       — Forwards tasting log to Google Apps Script (Drunk tab)
POST /update-quantity   — Decrements qty in Cellar; deletes row if qty reaches 0
GET  /health            — Liveness probe for Railway
"""

import os
import re
import json
import httpx
import anthropic

from datetime import date
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Wine Cellar API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Lazy clients ─────────────────────────────────────────────────────────────

_anthropic_client: Optional[anthropic.Anthropic] = None


def anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def apps_script_url() -> str:
    url = os.environ.get("APPS_SCRIPT_URL")
    if not url:
        raise RuntimeError("APPS_SCRIPT_URL environment variable not set")
    return url


# ─── Prompts ──────────────────────────────────────────────────────────────────

WINE_PROMPT = """\
You are a Master Sommelier with encyclopaedic knowledge of world wines.

Study this wine label photograph carefully. Then use web search to find:
  • Critic ratings — James Halliday (Australian Wine Companion), Robert Parker \
(Wine Advocate), Wine Spectator, Decanter, Vinous. Include score AND publication AND year.
  • Retail price in AUD — search Dan Murphy's, Cellarmaster, Wine.com.au, \
the winery's own website.
  • Professional tasting notes (2–4 sentences covering aromas, palate, finish).
  • Recommended drinking window (drink from year / drink by year).
  • Food pairings.

Return ONLY a valid JSON object — no markdown fences, no preamble, no trailing text:

{
  "name":       "wine name as printed on label, or null",
  "winery":     "producer / estate name, or null",
  "vintage":    "four-digit year as string, or null",
  "region":     "region and country, e.g. Barossa Valley, South Australia, or null",
  "grape":      "primary variety or blend description, or null",
  "rating":     "e.g. '97pts James Halliday Australian Wine Companion 2024', or null",
  "price_aud":  120,
  "notes":      "2–4 sentence tasting note, or null",
  "drink_from": "year as string, or null",
  "drink_to":   "year as string, or null",
  "food":       "comma-separated food pairings, or null",
  "confidence": {
    "name":      "high|medium|low",
    "winery":    "high|medium|low",
    "vintage":   "high|medium|low",
    "region":    "high|medium|low",
    "grape":     "high|medium|low",
    "rating":    "high|medium|low",
    "price_aud": "high|medium|low",
    "notes":     "high|medium|low"
  }
}

Confidence guide: "high" = certain from label or verified search result; \
"medium" = reasonable inference; "low" = best guess, not verified.
"""

# ─── Request models ───────────────────────────────────────────────────────────


class AnalyseRequest(BaseModel):
    image: str                      # base64-encoded image bytes
    media_type: str = "image/jpeg"  # MIME type of the image


class AddWineRequest(BaseModel):
    name: str
    winery: str = ""
    vintage: str = ""
    region: str = ""
    grape: str = ""
    rating: str = ""
    price_aud: str = ""
    quantity: int = 1
    notes: str = ""
    food: str = ""
    drink_from: str = ""
    drink_to: str = ""
    date_added: str = ""


class UpdateQuantityRequest(BaseModel):
    name: str
    vintage: str = ""
    winery: str = ""
    quantity_change: int = -1


class MarkTastedRequest(BaseModel):
    # Original wine fields
    name: str
    winery: str = ""
    vintage: str = ""
    region: str = ""
    grape: str = ""
    rating: str = ""
    price_aud: str = ""
    quantity: int = 1
    notes: str = ""
    date_added: str = ""
    # Tasting log fields
    my_score: str = ""
    my_notes: str = ""
    date_drunk: str = ""


# ─── Routes ───────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyse-wine")
async def analyse_wine(req: AnalyseRequest):
    """
    Send a wine label photo to Claude claude-sonnet-4-6 with vision + web search.
    Returns a structured JSON object with all wine details.
    """
    client = anthropic_client()

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": req.media_type,
                        "data": req.image,
                    },
                },
                {"type": "text", "text": WINE_PROMPT},
            ],
        }
    ]

    final_text = ""

    try:
        # Agentic loop — handles Claude's web_search tool calls transparently.
        # When Claude uses web search the API returns stop_reason="tool_use";
        # we acknowledge each tool call and let Claude continue until end_turn.
        for _iteration in range(10):
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=messages,
            )

            # Collect any text blocks in this turn
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    final_text += block.text

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "tool_use":
                # Append Claude's turn, then send back tool results
                messages.append({"role": "assistant", "content": response.content})
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search executed.",
                    }
                    for block in response.content
                    if hasattr(block, "type") and block.type == "tool_use"
                ]
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                continue

            # Any other stop reason (e.g. max_tokens) — exit loop
            break

    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API unreachable: {exc}")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Extract JSON from the accumulated text
    match = re.search(r"\{[\s\S]*\}", final_text)
    if not match:
        raise HTTPException(
            status_code=502,
            detail="Claude did not return a parseable JSON object. Raw response: "
                   + final_text[:300],
        )

    try:
        wine_data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"JSON parse error: {exc}")

    return wine_data


async def _post_to_sheets(payload: dict) -> dict:
    """POST a named-field payload to the Google Apps Script webhook."""
    url = apps_script_url()
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                follow_redirects=True,
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Google Sheets request timed out")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Apps Script returned HTTP {exc.response.status_code}",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Sheets sync error: {exc}")

    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("message", "Unknown Sheets error"))

    return result


@app.post("/add-wine")
async def add_wine(req: AddWineRequest):
    """Forward a new wine to the Cellar tab in Google Sheets."""
    today = date.today().isoformat()
    notes_combined = "\n".join(
        filter(None, [req.notes, f"Food: {req.food}" if req.food else ""])
    )
    payload = {
        "action": "add_wine",
        "name": req.name,
        "vintage": req.vintage,
        "region": req.region,
        "grape": req.grape,
        "winery": req.winery,
        "rating": req.rating,
        "price": f"${req.price_aud}" if req.price_aud else "",
        "quantity": req.quantity,
        "date_added": req.date_added or today,
        "notes": notes_combined,
    }
    result = await _post_to_sheets(payload)
    return {"status": "ok", "message": "Wine added to cellar", "sheets": result}


@app.post("/mark-tasted")
async def mark_tasted(req: MarkTastedRequest):
    """Forward a tasting log to the Drunk tab in Google Sheets."""
    today = date.today().isoformat()
    payload = {
        "action": "add_drunk",
        "name": req.name,
        "vintage": req.vintage,
        "region": req.region,
        "grape": req.grape,
        "winery": req.winery,
        "rating": req.rating,
        "price": f"${req.price_aud}" if req.price_aud else "",
        "quantity": req.quantity,
        "date_added": req.date_added or today,
        "notes": req.notes,
        "date_drunk": req.date_drunk or today,
        "my_score": req.my_score,
        "my_notes": req.my_notes,
    }
    result = await _post_to_sheets(payload)
    return {"status": "ok", "message": "Tasting logged", "sheets": result}


@app.post("/update-quantity")
async def update_quantity(req: UpdateQuantityRequest):
    """
    Decrement (or adjust) the quantity of a wine in the Cellar sheet.
    If the new quantity reaches 0 the Apps Script deletes the row entirely.
    """
    payload = {
        "action": "update_quantity",
        "name": req.name,
        "vintage": req.vintage,
        "winery": req.winery,
        "quantity_change": req.quantity_change,
    }
    print(f"[update-quantity] payload → {payload}")
    result = await _post_to_sheets(payload)
    return {"status": "ok", "message": "Quantity updated", "sheets": result}
