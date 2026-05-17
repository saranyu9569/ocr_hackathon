import streamlit as st
import pandas as pd
import json
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Table, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from main import run_pipeline


# =========================
# CACHE
# =========================
@st.cache_data
def run_cached(file_bytes):
    with open("temp.jpg", "wb") as f:
        f.write(file_bytes)

    return run_pipeline("temp.jpg")

# =========================
# EXPORT JSON
# =========================
def to_json(data):
    output_dict = {
        "vendor_name": data.vendor_name,
        "transaction_date": data.transaction_date,
        "total_amount": data.total_amount,
        "items": [
            {
                "name": item.name,
                "price": item.price
            }
            for item in data.items
        ]
    }

    return json.dumps(
        output_dict,
        ensure_ascii=False,
        indent=2
    )


# =========================
# PAGE
# =========================
st.set_page_config(
    page_title="OCR Receipt",
    layout="wide"
)

st.title("Receipt OCR Extractor")

uploaded_file = st.file_uploader(
    "Upload receipt image",
    type=["jpg", "jpeg", "png"]
)


# =========================
# MAIN FLOW
# =========================
if uploaded_file:

    left_col, right_col = st.columns([1, 3])

    with left_col:
        st.image(
            uploaded_file,
            caption="Uploaded Image",
            width="stretch"
        )

        extract_btn = st.button("Extract", width="stretch")

    # Run extraction and store in session state
    if extract_btn:
        with right_col:
            with st.status("🔍 Extracting receipt details...", expanded=True) as status:
                st.write("📤 Uploading image...")
                import time as _t; _t.sleep(0.3)

                st.write("🔎 Analyzing receipt...")
                import io as _io, contextlib as _ctx

                try:
                    # Suppress noisy console output from pipeline
                    with _ctx.redirect_stdout(_io.StringIO()):
                        data = run_cached(
                            uploaded_file.getvalue()
                        )

                    st.write("📋 Extracting items and details...")
                    _t.sleep(0.3)

                    st.session_state["extracted_data"] = data
                    status.update(
                        label="✅ Extraction complete!",
                        state="complete",
                        expanded=False
                    )

                except Exception as e:
                    status.update(
                        label="❌ Extraction failed",
                        state="error",
                        expanded=True
                    )
                    if "429" in str(e) or "quota" in str(e).lower():
                        st.error(
                            "⚠️ API quota เต็มแล้ว กรุณาลองใหม่ภายหลัง"
                        )
                    else:
                        st.error(
                            f"❌ เกิดข้อผิดพลาด: {e}"
                        )
                    st.stop()

    # Display results from session state
    if "extracted_data" in st.session_state:
        data = st.session_state["extracted_data"]

        with right_col:
            # =========================
            # EDITABLE HEADER INFO
            # =========================
            st.subheader("Receipt Info")

            vendor = st.text_input(
                "Vendor",
                value=data.vendor_name or ""
            )

            c1, c2 = st.columns(2)

            with c1:
                date = st.text_input(
                    "Date",
                    value=data.transaction_date or ""
                )

            with c2:
                total = st.number_input(
                    "Total",
                    value=float(data.total_amount or 0),
                    format="%.2f"
                )

            # =========================
            # EDITABLE ITEMS TABLE
            # =========================
            st.subheader("Items")

            df = pd.DataFrame([
                {
                    "Name": item.name,
                    "Price": float(item.price)
                }
                for item in data.items
            ])

            edited_df = st.data_editor(
                df,
                width="stretch",
                num_rows="dynamic"
            )

            # =========================
            # DOWNLOAD (uses edited values)
            # =========================
            edited_output = {
                "vendor_name": vendor,
                "transaction_date": date,
                "total_amount": total,
                "items": [
                    {"name": row["Name"], "price": row["Price"]}
                    for _, row in edited_df.iterrows()
                ]
            }

            json_data = json.dumps(
                edited_output,
                ensure_ascii=False,
                indent=2
            )

            excel_buffer = BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                header_df = pd.DataFrame([
                    {"Field": "Vendor", "Value": vendor},
                    {"Field": "Date", "Value": date},
                    {"Field": "Total", "Value": total},
                ])
                header_df.to_excel(
                    writer,
                    sheet_name="Receipt",
                    index=False,
                    startrow=0
                )
                edited_df.to_excel(
                    writer,
                    sheet_name="Receipt",
                    index=False,
                    startrow=len(header_df) + 2
                )
            excel_buffer.seek(0)

            dl1, dl2 = st.columns(2)

            with dl1:
                st.download_button(
                    label="📥 Download Excel",
                    data=excel_buffer,
                    file_name="receipt.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

            with dl2:
                st.download_button(
                    label="📥 Download JSON",
                    data=json_data,
                    file_name="receipt.json",
                    mime="application/json"
                )

