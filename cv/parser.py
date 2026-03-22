import logging
import re

logger = logging.getLogger(__name__)


def extract_with_pdfplumber(pdf_path: str) -> str:
    """Extract text using pdfplumber — good for columnar layouts"""
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        return text
    except Exception:
        return ""


def extract_with_pymupdf(pdf_path: str) -> str:
    """Extract text using PyMuPDF — good for Hebrew"""
    try:
        import fitz
        text = ""
        doc = fitz.open(pdf_path)
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except Exception:
        return ""


def extract_with_vision(pdf_path: str) -> str:
    """Send PDF pages as images to Claude Vision — fallback for scanned/image PDFs"""
    try:
        import fitz
        import anthropic
        import base64
        
        client = anthropic.Anthropic()
        doc = fitz.open(pdf_path)
        full_text = ""
        
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode()
            
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64
                            }
                        },
                        {
                            "type": "text",
                            "text": "חלץ את כל הטקסט מהתמונה הזו. זו עמוד מתוך קורות חיים בעברית/אנגלית. החזר רק את הטקסט, בלי הסברים."
                        }
                    ]
                }]
            )
            full_text += response.content[0].text + "\n"
        
        doc.close()
        return full_text
    except Exception:
        return ""


def clean_text(text: str) -> str:
    """Basic text cleanup"""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[^\w\s\u0590-\u05FF.,@\-+():/\'"!?#&]', ' ', text)
    return text.strip()


def smart_extract(pdf_path: str) -> str:
    """Try multiple methods, pick the best result"""

    # Method 1 — pdfplumber
    text_plumber = extract_with_pdfplumber(pdf_path)
    logger.info(f"pdfplumber extracted {len(text_plumber)} chars")

    # Method 2 — PyMuPDF
    text_pymupdf = extract_with_pymupdf(pdf_path)
    logger.info(f"PyMuPDF extracted {len(text_pymupdf)} chars")

    # Pick the longer one
    best = text_plumber if len(text_plumber) > len(text_pymupdf) else text_pymupdf

    # If too short — probably a scanned/image PDF → use Claude Vision
    if len(best.strip()) < 100:
        logger.info("Text too short, falling back to Claude Vision OCR")
        best = extract_with_vision(pdf_path)
        logger.info(f"Vision extracted {len(best)} chars")

    return clean_text(best)
