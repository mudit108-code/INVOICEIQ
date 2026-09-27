"""
InvoiceIQ — AI-Powered Invoice Automation

"""

import os
import io
import uuid
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from utils.extraction import extract, ExtractionResult, ExtractedService
from utils.pricing import load_catalog, resolve_all, match_service, PriceMatch
from utils.invoice_pdf import build_invoice_pdf

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
LOG_PATH = os.path.join(DATA_DIR, "invoices_log.csv")
DEFAULT_CATALOG_CSV = os.path.join(DATA_DIR, "pricing_catalog.csv")
DEFAULT_CATALOG_XLSX = os.path.join(DATA_DIR, "pricing_catalog.xlsx")

st.set_page_config(page_title="InvoiceIQ", page_icon="🧾", layout="wide")

# ----------------------------------------------------------------------------
# Session state bootstrap
# ----------------------------------------------------------------------------
defaults = {
    "step": 1,
    "extraction": None,
    "line_items": [],           
    "company": {
        "name": "Nexora Digital Solutions",
        "address": "Plot 22, Hitech City Road, Hyderabad, Telangana 500081",
        "email": "billing@nexoradigital.in",
        "phone": "+91 98765 43210",
        "gstin": "36AAACN1234A1Z5",
    },
    "invoice_number": None,
    "discount_percent": 0.0,
    "approved": False,
    "raw_text": "",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def next_invoice_number() -> str:
    if os.path.exists(LOG_PATH):
        try:
            df = pd.read_csv(LOG_PATH)
            n = len(df) + 1
        except Exception:
            n = 1
    else:
        n = 1
    return f"INV-{date.today().strftime('%Y%m')}-{n:04d}"


def log_invoice(row: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    df_new = pd.DataFrame([row])
    if os.path.exists(LOG_PATH):
        df_new.to_csv(LOG_PATH, mode="a", header=False, index=False)
    else:
        df_new.to_csv(LOG_PATH, mode="w", header=True, index=False)


def reset_workflow():
    for k in ["step", "extraction", "line_items", "invoice_number", "approved", "raw_text"]:
        st.session_state[k] = defaults[k] if k != "line_items" else []
    st.session_state.step = 1


# ----------------------------------------------------------------------------
# Sidebar: configuration
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🧾 InvoiceIQ")
    st.caption("AI-powered invoice automation")

    nav = st.radio("Navigate", ["New Invoice", "Dashboard", "Pricing Catalog", "Settings"], index=0)

    st.divider()
    st.markdown("**Extraction Engine**")
    st.success("🧠 InvoiceIQ NLP — 100% local, no API key, no internet required")
    st.caption("Rule-based entity extraction + category classification.")

    st.divider()
    st.markdown("**Price Source**")
    source_choice = st.radio("Catalog format", ["CSV", "Excel"], horizontal=True)
    uploaded_catalog = st.file_uploader(
        f"Upload custom {source_choice} catalog (optional)",
        type=["csv"] if source_choice == "CSV" else ["xlsx"],
    )

# Resolve catalog path/buffer
if uploaded_catalog is not None:
    catalog_source = uploaded_catalog
    catalog_label = uploaded_catalog.name
else:
    catalog_source = DEFAULT_CATALOG_CSV if source_choice == "CSV" else DEFAULT_CATALOG_XLSX
    catalog_label = os.path.basename(catalog_source)

try:
    if hasattr(catalog_source, "read"):
        # uploaded file-like object
        if source_choice == "CSV":
            catalog_df = pd.read_csv(catalog_source)
        else:
            catalog_df = pd.read_excel(catalog_source, sheet_name=0)
        catalog_df.columns = [c.strip().lower().replace(" ", "_") for c in catalog_df.columns]
        catalog_df["status"] = catalog_df["status"].astype(str).str.lower()
        catalog_df = catalog_df[catalog_df["status"] == "active"].copy()
        catalog_df["unit_price_inr"] = pd.to_numeric(catalog_df["unit_price_inr"], errors="coerce")
        catalog_df["tax_rate_percent"] = pd.to_numeric(catalog_df["tax_rate_percent"], errors="coerce").fillna(0)
        catalog_df = catalog_df.dropna(subset=["unit_price_inr"]).reset_index(drop=True)
    else:
        catalog_df = load_catalog(catalog_source)
    catalog_error = None
except Exception as e:  # noqa: BLE001
    catalog_df = pd.DataFrame()
    catalog_error = str(e)

with st.sidebar:
    if catalog_error:
        st.error(f"Catalog load failed: {catalog_error}")
    else:
        st.caption(f"📊 Loaded **{len(catalog_df)}** active services from `{catalog_label}`")

# ============================================================================
# PAGE: Pricing Catalog
# ============================================================================
if nav == "Pricing Catalog":
    st.title("📊 Pricing Catalog")
    st.caption("This is the live price source. AI-extracted requests are matched against "
               "the `service_name` column. Never edited automatically — only humans update prices.")
    if not catalog_error:
        st.dataframe(catalog_df, use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        c1.download_button("⬇️ Download as CSV", catalog_df.to_csv(index=False).encode(),
                            "pricing_catalog.csv", "text/csv")
        buf = io.BytesIO()
        catalog_df.to_excel(buf, index=False)
        c2.download_button("⬇️ Download as Excel", buf.getvalue(), "pricing_catalog.xlsx")
    st.stop()

# ============================================================================
# PAGE: Settings
# ============================================================================
if nav == "Settings":
    st.title("⚙️ Company / Invoice Settings")
    comp = st.session_state.company
    c1, c2 = st.columns(2)
    with c1:
        comp["name"] = st.text_input("Company Name", comp["name"])
        comp["email"] = st.text_input("Billing Email", comp["email"])
        comp["gstin"] = st.text_input("GSTIN / Tax ID", comp["gstin"])
    with c2:
        comp["address"] = st.text_area("Address", comp["address"], height=100)
        comp["phone"] = st.text_input("Phone", comp["phone"])
    st.success("Saved automatically to this session.")
    st.stop()

# ============================================================================
# PAGE: Dashboard
# ============================================================================
if nav == "Dashboard":
    st.title("📈 Invoice Dashboard")
    if not os.path.exists(LOG_PATH):
        st.info("No invoices generated yet. Create your first invoice under **New Invoice**.")
        st.stop()
    log_df = pd.read_csv(LOG_PATH)
    log_df["invoice_date"] = pd.to_datetime(log_df["invoice_date"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Invoices", len(log_df))
    c2.metric("Total Billed", f"₹{log_df['grand_total'].sum():,.0f}")
    c3.metric("Avg Invoice Value", f"₹{log_df['grand_total'].mean():,.0f}")
    c4.metric("Flagged-for-Review Lines (all time)", int(log_df["review_flags"].sum()))

    st.subheader("Revenue Over Time")
    by_month = log_df.set_index("invoice_date").resample("D")["grand_total"].sum().reset_index()
    st.line_chart(by_month, x="invoice_date", y="grand_total")

    st.subheader("Recent Invoices")
    st.dataframe(
        log_df.sort_values("invoice_date", ascending=False)
        [["invoice_number", "invoice_date", "customer_name", "customer_email",
          "subtotal", "tax_total", "grand_total", "review_flags", "engine"]],
        use_container_width=True, hide_index=True,
    )
    st.download_button("⬇️ Export invoice log (CSV)", log_df.to_csv(index=False).encode(),
                        "invoices_log.csv", "text/csv")
    st.stop()

# ============================================================================
# PAGE: New Invoice — the core wizard
# ============================================================================
st.title("🧾 InvoiceIQ")
st.caption("Customer Requirement → AI Extraction → Price Lookup → Review → Invoice")

steps = ["1. Requirement", "2. AI Extraction", "3. Price Lookup", "4. Review & Approve", "5. Export"]
st.progress((st.session_state.step - 1) / 4)
cols = st.columns(5)
for i, label in enumerate(steps, start=1):
    with cols[i - 1]:
        if i == st.session_state.step:
            st.markdown(f"**➡️ {label}**")
        elif i < st.session_state.step:
            st.markdown(f"✅ {label}")
        else:
            st.markdown(f"⬜ {label}")
st.divider()

# ---------------- STEP 1: Requirement input ----------------
if st.session_state.step == 1:
    st.subheader("Step 1 — Paste the customer's requirement")
    example = ("Hi, this is Rahul Verma from BrightLeaf Retail. We need a dynamic website with "
               "a CMS, plus 2 landing pages for our festive campaign, and monthly SEO management "
               "for at least 6 months. Also send over 10 product photography shoots. "
               "My email is rahul.verma@brightleaf.co.in and phone is 9876512345. "
               "Note: please prioritize the website first, it's urgent.")
    st.session_state.raw_text = st.text_area(
        "Customer message (email, WhatsApp text, call notes — any plain language)",
        value=st.session_state.raw_text or "", height=180,
        placeholder=example,
    )
    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("Use example"):
            st.session_state.raw_text = example
            st.rerun()
    with c2:
        run = st.button("🚀 Extract with AI", type="primary", use_container_width=True)
    if run:
        if not st.session_state.raw_text.strip():
            st.warning("Please paste a customer requirement first.")
        else:
            with st.spinner("Reading requirement and extracting structured details..."):
                result = extract(st.session_state.raw_text)
            st.session_state.extraction = result
            st.session_state.step = 2
            st.rerun()

# ---------------- STEP 2: AI Extraction review ----------------
elif st.session_state.step == 2:
    st.subheader("Step 2 — AI-Extracted Details (editable)")
    result: ExtractionResult = st.session_state.extraction

    st.info("Extracted using the InvoiceIQ NLP engine.")
    if result.error:
        st.warning(result.error)

    c1, c2, c3 = st.columns(3)
    name = c1.text_input("Customer Name", result.customer_name or "")
    email = c2.text_input("Customer Email", result.customer_email or "")
    phone = c3.text_input("Customer Phone", result.customer_phone or "")
    notes = st.text_area("Notes", result.notes or "", height=70)

    st.markdown("##### Requested Services (as understood by the NLP engine — edit before pricing)")
    st.caption("`Predicted Category` comes from local keyword classification and is used "
               "to help disambiguate the price lookup in the next step.")
    if not result.requested_services:
        st.warning("No services were detected. Go back and rephrase, or add manually below.")
        svc_df = pd.DataFrame(columns=["service_guess", "quantity", "predicted_category"])
    else:
        svc_df = pd.DataFrame([{"service_guess": s.service_guess, "quantity": s.quantity,
                                 "predicted_category": s.predicted_category or ""}
                                for s in result.requested_services])

    edited = st.data_editor(
        svc_df, num_rows="dynamic", use_container_width=True,
        column_config={
            "service_guess": st.column_config.TextColumn("What the customer asked for", width="large"),
            "quantity": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0),
            "predicted_category": st.column_config.TextColumn("Predicted Category", width="medium"),
        },
        key="svc_editor",
    )

    b1, b2 = st.columns(2)
    if b1.button("⬅️ Back"):
        st.session_state.step = 1
        st.rerun()
    if b2.button("🔎 Look Up Prices →", type="primary"):
        result.customer_name = name
        result.customer_email = email
        result.customer_phone = phone
        result.notes = notes
        result.requested_services = [
            ExtractedService(raw_text=r["service_guess"], service_guess=r["service_guess"],
                              quantity=float(r["quantity"] or 1),
                              predicted_category=(str(r["predicted_category"]).strip() or None))
            for _, r in edited.iterrows() if str(r["service_guess"]).strip()
        ]
        st.session_state.extraction = result
        st.session_state.step = 3
        st.rerun()

# ---------------- STEP 3: Price lookup ----------------
elif st.session_state.step == 3:
    st.subheader("Step 3 — Price Lookup Against Catalog")
    st.caption("Matched automatically where confidence is high. Nothing is priced without a "
               "catalog match or explicit manual entry — the AI never fabricates prices.")

    result: ExtractionResult = st.session_state.extraction
    queries = [s.service_guess for s in result.requested_services]
    categories = [s.predicted_category for s in result.requested_services]

    if catalog_error:
        st.error("Cannot look up prices — catalog failed to load.")
    else:
        matches = resolve_all(queries, catalog_df, categories)
        line_items = []
        for s, m in zip(result.requested_services, matches):
            st.markdown(f"**Request:** “{s.raw_text}”  ·  qty **{s.quantity:g}**")
            if m.status == "MATCHED":
                st.success(f"✅ Matched → **{m.service_name}** ({m.category}) — "
                           f"₹{m.unit_price:,.2f} / {m.unit}  ·  confidence {m.confidence:.0%}")
                chosen_id = m.service_id
                chosen_price = m.unit_price
                chosen_name = m.service_name
                chosen_unit = m.unit
                chosen_tax = m.tax_rate
                needs_review = False
            else:
                st.error(f"🚩 NEEDS REVIEW — no confident catalog match (best guess "
                         f"confidence {m.confidence:.0%}). Select the correct service or price manually.")
                options = ["-- Manual entry / no match --"] + [
                    f"{sug['service_name']} (₹{sug['unit_price']:,.0f})" for sug in (m.suggestions or [])
                ]
                pick = st.selectbox("Suggestions from catalog", options, key=f"sugg_{s.raw_text}_{id(s)}")
                if pick != options[0] and m.suggestions:
                    idx = options.index(pick) - 1
                    sug = m.suggestions[idx]
                    row = catalog_df[catalog_df["service_id"] == sug["service_id"]].iloc[0]
                    chosen_id, chosen_price = sug["service_id"], sug["unit_price"]
                    chosen_name, chosen_unit, chosen_tax = row["service_name"], row["unit"], row["tax_rate_percent"]
                    needs_review = False
                    st.info("Suggestion accepted — will be included, still marked as AI-assisted match.")
                else:
                    manual_col1, manual_col2 = st.columns(2)
                    chosen_name = manual_col1.text_input("Service name for invoice", s.service_guess,
                                                           key=f"name_{id(s)}")
                    chosen_price = manual_col2.number_input("Manual unit price (₹)", min_value=0.0,
                                                              step=100.0, key=f"price_{id(s)}")
                    chosen_id, chosen_unit, chosen_tax = "MANUAL", "unit", 18.0
                    needs_review = chosen_price == 0.0
                    if needs_review:
                        st.warning("⚠️ Price not set — this line will block approval until priced.")

            line_items.append({
                "service_id": chosen_id, "service_name": chosen_name, "unit": chosen_unit,
                "quantity": s.quantity, "unit_price": chosen_price, "tax_rate": chosen_tax,
                "needs_review": needs_review,
            })
            st.divider()

        st.session_state.line_items = line_items

    b1, b2 = st.columns(2)
    if b1.button("⬅️ Back"):
        st.session_state.step = 2
        st.rerun()
    if b2.button("📝 Continue to Review →", type="primary"):
        st.session_state.step = 4
        st.rerun()

# ---------------- STEP 4: Review & Approve ----------------
elif st.session_state.step == 4:
    st.subheader("Step 4 — Review & Approve")
    result: ExtractionResult = st.session_state.extraction

    c1, c2, c3 = st.columns(3)
    c1.metric("Customer", result.customer_name or "—")
    c2.metric("Email", result.customer_email or "—")
    c3.metric("Urgency", (result.urgency or "normal").title())

    items_df = pd.DataFrame(st.session_state.line_items)
    st.markdown("##### Line Items (final edit before invoice)")
    edited_items = st.data_editor(
        items_df, use_container_width=True, num_rows="dynamic", key="review_editor",
        column_config={
            "service_id": st.column_config.TextColumn("ID", disabled=True, width="small"),
            "service_name": st.column_config.TextColumn("Service", width="large"),
            "unit": st.column_config.TextColumn("Unit", width="small"),
            "quantity": st.column_config.NumberColumn("Qty", min_value=0.0),
            "unit_price": st.column_config.NumberColumn("Unit Price (₹)", min_value=0.0, format="₹%.2f"),
            "tax_rate": st.column_config.NumberColumn("Tax %", min_value=0.0, max_value=100.0),
            "needs_review": st.column_config.CheckboxColumn("Needs Review"),
        },
    )
    st.session_state.line_items = edited_items.to_dict("records")

    flagged = [i for i in st.session_state.line_items if i.get("needs_review") or not i.get("unit_price")]
    subtotal = sum(float(i["quantity"]) * float(i["unit_price"] or 0) for i in st.session_state.line_items)
    tax_total = sum(float(i["quantity"]) * float(i["unit_price"] or 0) * float(i.get("tax_rate") or 0) / 100
                     for i in st.session_state.line_items)

    st.session_state.discount_percent = st.slider("Discount %", 0.0, 50.0,
                                                    float(st.session_state.discount_percent), 0.5)
    discount_amt = subtotal * st.session_state.discount_percent / 100
    grand_total = subtotal - discount_amt + tax_total

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Subtotal", f"₹{subtotal:,.2f}")
    mc2.metric("Discount", f"-₹{discount_amt:,.2f}")
    mc3.metric("Tax", f"₹{tax_total:,.2f}")
    mc4.metric("Grand Total", f"₹{grand_total:,.2f}")

    if flagged:
        st.error(f"🚩 {len(flagged)} line item(s) still need review/pricing before approval. "
                  "Fix them in the table above (set a unit price and uncheck 'Needs Review').")
    else:
        st.success("All line items priced and clean. Ready for approval.")

    if not result.customer_name or not result.customer_email:
        st.warning("Customer name and/or email is missing — add it below before approving.")
        cc1, cc2 = st.columns(2)
        result.customer_name = cc1.text_input("Customer Name (required)", result.customer_name or "")
        result.customer_email = cc2.text_input("Customer Email (required)", result.customer_email or "")

    b1, b2, b3 = st.columns(3)
    if b1.button("⬅️ Back to Pricing"):
        st.session_state.step = 3
        st.rerun()
    approve_disabled = bool(flagged) or not result.customer_name or not result.customer_email
    if b2.button("✅ Approve Invoice", type="primary", disabled=approve_disabled):
        st.session_state.invoice_number = next_invoice_number()
        st.session_state.approved = True
        st.session_state.step = 5
        st.rerun()
    if b3.button("🔄 Start Over"):
        reset_workflow()
        st.rerun()

# ---------------- STEP 5: Export ----------------
elif st.session_state.step == 5:
    st.subheader("Step 5 — Approved Invoice Ready")
    result: ExtractionResult = st.session_state.extraction
    items = st.session_state.line_items

    inv_date = date.today()
    due_date = inv_date + timedelta(days=15)

    pdf_bytes = build_invoice_pdf(
        invoice_number=st.session_state.invoice_number,
        invoice_date=inv_date,
        due_date=due_date,
        company=st.session_state.company,
        customer={"name": result.customer_name, "email": result.customer_email,
                  "phone": result.customer_phone},
        line_items=items,
        notes=result.notes,
        discount_percent=st.session_state.discount_percent,
    )

    subtotal = sum(float(i["quantity"]) * float(i["unit_price"] or 0) for i in items)
    tax_total = sum(float(i["quantity"]) * float(i["unit_price"] or 0) * float(i.get("tax_rate") or 0) / 100
                     for i in items)
    discount_amt = subtotal * st.session_state.discount_percent / 100
    grand_total = subtotal - discount_amt + tax_total

    st.success(f"Invoice **{st.session_state.invoice_number}** approved and generated ✅")
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown(f"**Customer:** {result.customer_name} · {result.customer_email}")
        st.markdown(f"**Date:** {inv_date.strftime('%d %b %Y')}  ·  **Due:** {due_date.strftime('%d %b %Y')}")
        st.markdown(f"**Total: ₹{grand_total:,.2f}**")
    with c2:
        st.download_button("⬇️ Download Invoice PDF", pdf_bytes,
                            file_name=f"{st.session_state.invoice_number}.pdf",
                            mime="application/pdf", type="primary", use_container_width=True)

    if "logged_for" not in st.session_state or st.session_state.get("logged_for") != st.session_state.invoice_number:
        log_invoice({
            "invoice_number": st.session_state.invoice_number,
            "invoice_date": inv_date.isoformat(),
            "customer_name": result.customer_name,
            "customer_email": result.customer_email,
            "subtotal": round(subtotal, 2),
            "tax_total": round(tax_total, 2),
            "discount_percent": st.session_state.discount_percent,
            "grand_total": round(grand_total, 2),
            "review_flags": sum(1 for i in items if i.get("needs_review")),
            "engine": result.engine,
            "line_item_count": len(items),
        })
        st.session_state.logged_for = st.session_state.invoice_number

    st.divider()
    st.markdown("##### Final Line Items")
    st.dataframe(pd.DataFrame(items)[["service_name", "unit", "quantity", "unit_price", "tax_rate"]],
                 use_container_width=True, hide_index=True)

    if st.button("🆕 Create Another Invoice"):
        reset_workflow()
        st.rerun()
