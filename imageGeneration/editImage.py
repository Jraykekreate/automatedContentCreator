# generator.py
from typing import Dict, Any
from google import genai
from PIL import Image
from io import BytesIO
import requests

# Initialize client (make sure GOOGLE_API_KEY is set in your env)
client = genai.Client()

def generate_image(prompt: str, image_url: str) -> Dict[str, Any]:
    """
    Fetches image from URL and sends prompt + image to Gemini.
    Returns:
      - {"type": "image", "bytes": b"...", "mime": "image/png"} if an image is generated
      - {"type": "text", "text": "..."} if only text is returned
    """
    # Fetch image bytes from the URL
    resp = requests.get(image_url)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch image from {image_url} (status {resp.status_code})")
    pil_image = Image.open(BytesIO(resp.content))

    # Call the Gemini model
    response = client.models.generate_content(
        model="models/gemini-2.5-flash-image-preview",
        contents=[prompt, pil_image],
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
