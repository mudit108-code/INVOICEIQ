# 🧾 InvoiceIQ — AI-Powered Invoice Automation

InvoiceIQ turns a plain-language customer request ("Hi, we need a website, 10 product
photos and 6 months of SEO...") into a professional, review-ready PDF invoice.

```
Customer Requirement → NLP Extraction → Price Lookup (CSV/Excel) → Review & Approval → PDF Invoice
```

**100% local. No API keys. No internet required at runtime.** Every extraction,
match, and calculation happens in-process — nothing is sent to any
external service, ever.

## Quick start

```bash
cd invoiceiq
pip install -r requirements.txt
streamlit run app.py
```


## The workflow, step by step

1. **Requirement** — paste any free-text customer message (email, WhatsApp, call notes,etc..).
2. **NLP Extraction** — the local engine pulls out customer name, email, phone, requested
   services (in the customer's own words), quantities, a predicted category per service,
   urgency and notes. Everything is editable in an inline table before moving on.
3. **Price Lookup** — each requested service is fuzzy-matched against `pricing_catalog.csv`
   / `.xlsx` (category agreement gives a small confidence boost to break ties).
   High-confidence matches auto-fill; ambiguous ones show ranked suggestions;
   no-match lines require a manual price and are blocked from approval until set.
4. **Review & Approve** — final editable line-item grid, live subtotal/tax/discount/total,
   and an explicit **Approve** button that's disabled while anything is unresolved.
5. **Export** — a branded PDF invoice is generated and downloadable, and the invoice is
   logged to `data/invoices_log.csv` for the Dashboard.

## Data source integration

- Switch between **CSV** and **Excel** catalogs from the sidebar, or upload your own file
  in the same schema — the app re-reads it live, no restart needed.
- Catalog schema (`data/pricing_catalog.csv` / `.xlsx`):

  | column | meaning |
  |---|---|
  | `service_id` | stable ID, printed nowhere but used internally |
  | `service_name` | matched against AI-extracted requests |
  | `category` | grouping for reporting |
  | `unit` | package / project / month / hour / unit / page / report |
  | `unit_price_inr` | price per unit, in INR |
  | `tax_rate_percent` | GST/tax % applied per line |
  | `status` | `active` / `inactive` — inactive rows are hidden from matching |
  | `description` | shown in the catalog viewer |


## The local NLP engine (`utils/extraction.py`)

No spaCy, no transformers, no API calls — just carefully layered `re` patterns, which
makes the whole pipeline auditable line-by-line:

1. **Greeting/contact NER** — regex patterns for `"Hi, this is <Name> from <Company>"`
   style openers, plus dedicated email/phone patterns.
2. **Clause stripping** — the greeting, notes, and contact clauses are removed from the
   working text *before* segmentation, so they never get misread as a requested service.
3. **Segmentation** — splits on commas, `and`/`plus`/`also`, and sentence boundaries.
4. **Category classification** — each segment is scored against a keyword lexicon per
   catalog category (edit `CATEGORY_LEXICON` in `extraction.py` to match your own catalog).
5. **Quantity inference** — numbers plus unit words (`months`, `screens`, `products`,
   `hours`...) are parsed per segment.

This module **never touches price** — pricing is 100% deterministic and handled
separately in `pricing.py`, so nothing here can fabricate a cost.

## Bonus features included

- 📄 PDF export with a professional branded layout (`reportlab`)
- 💰 Per-line GST-style tax + invoice-level discount
- 📊 Dashboard: total billed, avg invoice value, revenue-over-time chart, invoice history
- 📇 Editable company profile (Settings page) used on every invoice
- 🔁 Auto-incrementing invoice numbers (`INV-YYYYMM-NNNN`)
- 📥 Bring-your-own catalog: upload a custom CSV/Excel at runtime


