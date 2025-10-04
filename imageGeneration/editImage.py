# generator.py
from typing import Dict, Any
from google import genai
from PIL import Image
from io import BytesIO
import requests
import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

# Initialize FastAPI app
app = FastAPI()

# Initialize Gemini client (ensure GOOGLE_API_KEY is set in your env)
client = genai.Client()


def fetch_image_from_url(url: str) -> Image.Image:
    """Fetch an image from a URL and return as PIL Image."""
    resp = requests.get(url)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch image from {url} (status {resp.status_code})")
    return Image.open(BytesIO(resp.content))


def generate_image(prompt: str, prompt_image_url: str, image_url: str) -> Dict[str, Any]:
    """
    Fetches images and sends prompt + images to Gemini.
    Returns:
      - {"type": "image", "bytes": b"...", "mime": "image/png"} if an image is generated
      - {"type": "text", "text": "..."} if only text is returned
    """
    # Fetch both images
    base_image = fetch_image_from_url(image_url)
    prompt_image = fetch_image_from_url(prompt_image_url)

    # Send prompt + both images to Gemini
    response = client.models.generate_content(
        model="models/gemini-2.5-flash-image-preview",
        contents=[prompt, prompt_image, base_image],
    )

    if not response.candidates:
        raise RuntimeError("No candidates returned from model")

    parts = response.candidates[0].content.parts
    text_parts = []

    for part in parts:
        if getattr(part, "text", None):
            text_parts.append(part.text)
        elif getattr(part, "inline_data", None):
            inline = part.inline_data
            data = inline.data  # raw bytes
            mime = getattr(inline, "mime_type", "image/png")
            return {"type": "image", "bytes": data, "mime": mime}

    if text_parts:
        return {"type": "text", "text": " ".join(text_parts)}

    raise RuntimeError("Model returned no image or text parts")