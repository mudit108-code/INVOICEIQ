"""
InvoiceIQ NLP Engine — Local Natural-Language Extraction
============================================================
100% local. No external API calls, no API keys, no internet dependency.

Turns a free-text customer requirement into structured data:
    { customer_name, customer_email, phone, requested_services: [
          {raw_text, service_guess, quantity, predicted_category, confidence}
      ], notes, urgency }

Pipeline (pure Python, stdlib `re` only):
  1. Contact-entity extraction   — email / phone via regex, name/company via
     greeting-pattern + title-case heuristics (a lightweight rule-based NER).
  2. Segmentation                — splits the message into individual service
     "asks" on natural boundaries (commas, and/plus/also, sentence breaks),
     after removing the greeting, contact and notes clauses so they never
     get misread as a requested service.
  3. Category classification     — each segment is scored against a keyword
     lexicon per catalog category (Design, Web Development, Digital
     Marketing, ...) to produce a `predicted_category` + confidence. This is
     used later by the price matcher to disambiguate near-tie matches.
  4. Quantity inference           — numeric quantities and their unit words
     (months, screens, pages, products, hours...) are parsed from each
     segment.

This module NEVER assigns a price — pricing is resolved separately and
deterministically against the catalog (see pricing.py), so nothing here can
fabricate a cost.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"(\+?\d{1,3}[-\s]?)?\d{10}\b")
QTY_RE = re.compile(
    r"\b(\d{1,4})\s*"
    r"(x|units?|pcs?|pages?|screens?|articles?|products?|months?|hours?|sessions?|shoots?)?\b",
    re.I,
)

INTRO_RE = re.compile(
    r"^(?:hi|hello|hey|dear\s+\w+)?[,.\s]*(?:this is|i am|i'm|my name is)\s+"
    r"([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2})"
    r"(?:\s+from\s+([A-Za-z0-9&.,'\- ]+?))?"
    r"(?=[.,\n]|$)",
    re.I,
)
NON_SERVICE_PHRASES = re.compile(
    r"\b(urgent|asap|regards|thanks|thank you|please prioriti[sz]e|note[s]?:|special instruction)\b",
    re.I,
)
LEADING_FILLER_RE = re.compile(
    r"^(i need|we need|we'd like|i'd like|want|please|also|require|looking for|"
    r"get me|can you|could you|would like|need|interested in|send over)\b[:\s]*",
    re.I,
)

# ----------------------------------------------------------------------------
# Rule-based category classifier — the keyword lexicon mirrors the demo
# catalog's categories so extraction and pricing "speak the same language".
# Edit this dict if you rename/add categories in your own catalog.
# ----------------------------------------------------------------------------
CATEGORY_LEXICON = {
    "Design": ["logo", "brand", "branding", "business card", "style guide",
               "graphic", "poster", "flyer", "ui", "ux", "design"],
    "Web Development": ["website", "web app", "web application", "landing page",
                         "cms", "wordpress", "e-commerce", "ecommerce", "web dev",
                         "site", "webpage"],
    "Digital Marketing": ["seo", "social media", "google ads", "ppc", "ads campaign",
                           "email marketing", "marketing", "campaign", "smm"],
    "Content": ["article", "blog", "content writing", "copywriting", "content"],
    "Photography": ["photography", "photo shoot", "photoshoot", "product photo",
                     "photos", "photography shoot"],
    "Video Production": ["video", "animation", "explainer", "videography"],
    "App Development": ["mobile app", "android app", "ios app", "react native",
                         "app development", "application"],
    "DevOps": ["hosting", "cloud", "aws", "gcp", "server setup", "devops"],
    "Development": ["api", "database", "integration", "qa", "testing", "backend",
                     "software development"],
    "Consulting": ["consulting", "training", "strategy", "consultation", "advisory"],
    "Infrastructure": ["domain", "ssl", "certificate", "hosting plan"],
}


@dataclass
class ExtractedService:
    raw_text: str
    service_guess: str
    quantity: float = 1
    predicted_category: Optional[str] = None
    category_confidence: float = 0.0


@dataclass
class ExtractionResult:
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    requested_services: List[ExtractedService] = field(default_factory=list)
    notes: Optional[str] = None
    urgency: Optional[str] = "normal"
    engine: str = "InvoiceIQ NLP (local)"
    error: Optional[str] = None


def _classify_category(phrase: str):
    """Score a phrase against the keyword lexicon. Returns (category, confidence)."""
    p = phrase.lower()
    best_cat, best_score = None, 0.0
    for cat, keywords in CATEGORY_LEXICON.items():
        hits = sum(1 for kw in keywords if kw in p)
        if hits == 0:
            continue
        score = min(1.0, 0.4 + 0.25 * hits)
        if score > best_score:
            best_cat, best_score = cat, score
    return best_cat, round(best_score, 2)


def extract(text: str) -> ExtractionResult:
    """Main entry point — pure local rule-based extraction, no network calls."""
    text = (text or "").strip()
    if not text:
        return ExtractionResult(error="Empty input")

    email_match = EMAIL_RE.search(text)
    phone_match = PHONE_RE.search(text)

    name = None
    company_hint = None
    intro_span = None
    m = INTRO_RE.search(text)
    if m:
        name = m.group(1).strip()
        company_hint = (m.group(2) or "").strip().rstrip(".") or None
        intro_span = m.span()

    # Strip the greeting/intro clause out before splitting into service
    # requests, so it never gets mistaken for a requested service.
    working = text
    if intro_span:
        working = working[:intro_span[0]] + working[intro_span[1]:]

    notes = None
    notes_match = re.search(r"(?:note|notes|special instruction)s?[:\-]\s*(.+)", working, re.I)
    if notes_match:
        notes = notes_match.group(1).strip().rstrip(".")
        working = working[:notes_match.start()]

    # Remove standalone email/phone clauses (e.g. "My email is x@y.com and phone is ...")
    working = EMAIL_RE.sub("", working)
    working = PHONE_RE.sub("", working)
    working = re.sub(r"[^.,;\n]*\b(email|e-mail|phone|contact number|call me)\b[^.,;\n]*",
                      "", working, flags=re.I)

    # Split into requirement-ish clauses on commas / "and" / "plus" / "also" /
    # sentence boundaries / line breaks (case-insensitive).
    clauses = re.split(r",|\band\b|\bplus\b|\balso\b|\.\s+|\n|;", working, flags=re.I)

    services = []
    stop_words = {"a", "the", "for", "our", "my", "we", "need", "want", "please",
                  "also", "an", "with", "of", "to", "us", "company", "business",
                  "is", "it's", "its", "urgent", "asap"}
    for clause in clauses:
        c = clause.strip(" .!?")
        if len(c) < 4:
            continue
        if NON_SERVICE_PHRASES.search(c):
            continue
        qty = 1
        qm = QTY_RE.search(c)
        if qm and qm.group(1):
            qty = float(qm.group(1))
        phrase = LEADING_FILLER_RE.sub("", c).strip(" .!?")
        phrase = re.sub(r"^\d+\s*(x|units?)?\s*", "", phrase, flags=re.I).strip()
        if not phrase:
            continue
        words = [w for w in re.findall(r"[a-zA-Z']+", phrase) if w.lower() not in stop_words]
        if len(words) < 2:
            continue
        category, cat_conf = _classify_category(phrase)
        services.append(ExtractedService(
            raw_text=c.strip(), service_guess=phrase, quantity=qty,
            predicted_category=category, category_confidence=cat_conf,
        ))

    urgency = "urgent" if re.search(r"\b(urgent|asap|immediately|rush)\b", text, re.I) else "normal"
    if company_hint and not notes:
        notes = f"Company: {company_hint}"
    elif company_hint:
        notes = f"{notes}  |  Company: {company_hint}"

    return ExtractionResult(
        customer_name=name,
        customer_email=email_match.group(0) if email_match else None,
        customer_phone=phone_match.group(0) if phone_match else None,
        requested_services=services[:15],
        notes=notes,
        urgency=urgency,
    )
