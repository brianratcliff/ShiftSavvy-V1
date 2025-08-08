# ShiftSavvy — Proof of Concept (Streamlit)

A lightweight demo of ShiftSavvy's core engine: jobs & pay rules, shifts, expenses, live dashboard, and CSV export.

## Run locally
1) Python 3.10+ installed
2) In terminal:
```
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
Open the URL shown (usually http://localhost:8501).

## Quick deploy (Streamlit Community Cloud)
1) Push this folder to a **public GitHub repo** (root must contain `app.py` and `requirements.txt`).
2) Go to https://streamlit.io/cloud → **New app**
3) Select your repo & branch → **Main file path:** `app.py` → **Deploy**.
4) You'll get a URL like `https://<your-app>.streamlit.app` you can open on your phone.

## Files
- `app.py` — Streamlit app
- `requirements.txt` — dependencies
- `README.md` — instructions
