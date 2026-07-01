import base64
import json
import re

import anthropic

_client = None
MODEL = "claude-sonnet-4-6"


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def extract_json(text: str) -> dict:
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


async def estimate_food(description: str) -> dict:
    """Estimate calories for a food/drink description.

    Returns: {"items": [{"name": str, "calories": int}], "total": int, "notes": str}
    """
    response = await get_client().messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": (
                f'You are a calorie estimation assistant. The user consumed: "{description}"\n\n'
                "Estimate calories for each item and the total.\n\n"
                "Respond ONLY with valid JSON in this exact format:\n"
                "{\n"
                '  "items": [\n'
                '    {"name": "item name", "calories": 90},\n'
                '    {"name": "coffee with milk", "calories": 55}\n'
                "  ],\n"
                '  "total": 145,\n'
                '  "notes": "Brief note about portion size assumptions"\n'
                "}\n\n"
                "Use typical portion sizes when not specified. Be concise."
            )
        }]
    )
    try:
        return extract_json(response.content[0].text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise ValueError(f"Could not parse Claude response: {e}") from e


async def estimate_food_from_photo(image_bytes: bytes, mime_type: str) -> dict:
    """Estimate calories from a food photo.

    Returns: {"items": [{"name": str, "calories": int}], "total": int, "notes": str}
    """
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = await get_client().messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "You are a calorie estimation assistant. Identify the food in this photo "
                        "and estimate calories.\n\n"
                        "Respond ONLY with valid JSON in this exact format:\n"
                        "{\n"
                        '  "items": [\n'
                        '    {"name": "identified food", "calories": 300}\n'
                        "  ],\n"
                        '  "total": 300,\n'
                        '  "notes": "What you see and any assumptions made"\n'
                        "}\n\n"
                        "If you cannot identify food, set total to 0 and explain in notes."
                    ),
                },
            ],
        }]
    )
    try:
        return extract_json(response.content[0].text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise ValueError(f"Could not parse Claude response: {e}") from e


async def correct_estimate(context_description: str, previous_result: dict, correction: str) -> dict:
    """Re-estimate calories given a correction from the user.

    Returns same shape as estimate_food().
    """
    response = await get_client().messages.create(
        model=MODEL,
        max_tokens=512,
        system=(
            "You are a calorie estimation assistant. "
            "Always respond with valid JSON: "
            '{"items": [{"name": str, "calories": int}], "total": int, "notes": str}'
        ),
        messages=[
            {
                "role": "user",
                "content": f'Estimate calories for: "{context_description}"',
            },
            {
                "role": "assistant",
                "content": json.dumps(previous_result),
            },
            {
                "role": "user",
                "content": f"Actually, {correction}. Please revise your estimate.",
            },
        ],
    )
    try:
        return extract_json(response.content[0].text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise ValueError(f"Could not parse Claude response: {e}") from e


async def estimate_workout(description: str, user_profile: dict) -> dict:
    """Estimate calories burned from a workout description.

    user_profile: {"gender": str, "height_cm": int, "weight_kg": int}
    Returns: {"activity": str, "duration_min": int, "calories_burned": int, "notes": str}
    """
    profile_str = (
        f"{user_profile['gender']}, "
        f"{user_profile['height_cm']}cm, "
        f"{user_profile['weight_kg']}kg"
    )
    response = await get_client().messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": (
                f"You are a fitness calorie calculator.\n\n"
                f"User profile: {profile_str}\n"
                f'Workout: "{description}"\n\n'
                "Use MET values or sport-specific formulas. For swimming, distance and heart rate "
                "are strong signals alongside body weight.\n\n"
                "Respond ONLY with valid JSON:\n"
                "{\n"
                '  "activity": "Swimming",\n'
                '  "duration_min": 47,\n'
                '  "calories_burned": 520,\n'
                '  "notes": "Based on 2050m at moderate pace, HR 133, body weight 108kg"\n'
                "}"
            )
        }]
    )
    try:
        return extract_json(response.content[0].text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise ValueError(f"Could not parse Claude response: {e}") from e
