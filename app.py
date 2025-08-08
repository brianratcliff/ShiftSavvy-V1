import streamlit as st
import sqlite3
import pandas as pd
from datetime import date
import matplotlib.pyplot as plt
from pathlib import Path

DB_PATH = Path(__file__).with_name("data.db")

# ---------------------- DB Helpers ----------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        currency TEXT DEFAULT '$',
        show_annual_projection INTEGER DEFAULT 1,
        week_starts_monday INTEGER DEFAULT 0
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        base_rate REAL NOT NULL,
        ot_rule TEXT NOT NULL CHECK(ot_rule IN ('weekly_40','daily_8')),
        ot_multiplier REAL NOT NULL DEFAULT 1.5,
        diff_type TEXT NOT NULL CHECK(diff_type IN ('percent','fixed')) DEFAULT 'percent',
        diff_value REAL NOT NULL DEFAULT 0.0,
        active INTEGER NOT NULL DEFAULT 1
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        shift_date TEXT NOT NULL,
        hours REAL NOT NULL,
        shift_kind TEXT NOT NULL DEFAULT 'Day',
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        expense_date TEXT NOT NULL,
        recurring INTEGER NOT NULL DEFAULT 0
    );
    """)

    # seed settings
    cur.execute("INSERT OR IGNORE INTO settings (id,currency,show_annual_projection,week_starts_monday) VALUES (1,'$',1,0);")

    # seed demo jobs if none
    cur.execute("SELECT COUNT(*) FROM jobs;")
    if cur.fetchone()[0] == 0:
        cur.execute("""
        INSERT INTO jobs (name, base_rate, ot_rule, ot_multiplier, diff_type, diff_value, active)
        VALUES
        ('RN - Staff', 38.0, 'weekly_40', 1.5, 'percent', 10.0, 1),
        ('RN - PRN', 55.0, 'daily_8', 1.5, 'fixed', 2.0, 1)
        """)

    # seed demo shifts & expenses if none
    cur.execute("SELECT COUNT(*) FROM shifts;")
    if cur.fetchone()[0] == 0:
        from datetime import timedelta
        today = date.today()
        demo_dates = [today - timedelta(days=i) for i in [1,2,3,5,6]]
        for d in demo_dates:
            cur.execute("INSERT INTO shifts (job_id, shift_date, hours, shift_kind) VALUES (1, ?, ?, ?)",
                        (d.isoformat(), 12 if d.weekday() in (5,6) else 10, 'Night' if d.weekday() in (5,6) else 'Day'))
    cur.execute("SELECT COUNT(*) FROM expenses;")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO expenses (category, amount, expense_date, recurring) VALUES ('Gas', 60.0, date('now'), 0)")
        cur.execute("INSERT INTO expenses (category, amount, expense_date, recurring) VALUES ('Food', 120.0, date('now'), 0)")
        cur.execute("INSERT INTO expenses (category, amount, expense_date, recurring) VALUES ('Rent', 1500.0, date('now','start of month'), 1)")

    conn.commit()
    conn.close()

# ---------------------- Business Logic ----------------------
def get_settings():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT currency, show_annual_projection, week_starts_monday FROM settings WHERE id=1;")
    row = cur.fetchone()
    conn.close()
    return {"currency": row[0], "show_annual": bool(row[1]), "week_monday": bool(row[2])}

def update_settings(currency, show_annual, week_monday):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE settings SET currency=?, show_annual_projection=?, week_starts_monday=? WHERE id=1;",
                (currency, 1 if show_annual else 0, 1 if week_monday else 0))
    conn.commit()
    conn.close()

def fetch_df(query, params=()):
    conn = get_conn()
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df

def list_jobs(active_only=True):
    query = "SELECT * FROM jobs"
    if active_only:
        query += " WHERE active=1"
    return fetch_df(query)

def add_job(name, base_rate, ot_rule, ot_multiplier, diff_type, diff_value, active=True):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO jobs (name, base_rate, ot_rule, ot_multiplier, diff_type, diff_value, active)
        VALUES (?,?,?,?,?,?,?)
    """, (name, base_rate, ot_rule, ot_multiplier, diff_type, diff_value, 1 if active else 0))
    conn.commit()
    conn.close()

def update_job(job_id, name, base_rate, ot_rule, ot_multiplier, diff_type, diff_value, active):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE jobs SET name=?, base_rate=?, ot_rule=?, ot_multiplier=?, diff_type=?, diff_value=?, active=?
        WHERE id=?
    """, (name, base_rate, ot_rule, ot_multiplier, diff_type, diff_value, 1 if active else 0, job_id))
    conn.commit()
    conn.close()

def delete_job(job_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()
    conn.close()

def add_shift(job_id, shift_date, hours, shift_kind):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO shifts (job_id, shift_date, hours, shift_kind)
        VALUES (?,?,?,?)
    """, (job_id, shift_date, hours, shift_kind))
    conn.commit()
    conn.close()

def add_expense(category, amount, expense_date, recurring):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO expenses (category, amount, expense_date, recurring)
        VALUES (?,?,?,?)
    """, (category, amount, expense_date, 1 if recurring else 0))
    conn.commit()
    conn.close()

def calc_differential(base_rate, diff_type, diff_value):
    if diff_type == 'percent':
        return base_rate * (diff_value / 100.0)
    else:
        return diff_value

def calc_shift_earnings(row, job):
    """Returns earnings for a single shift.
    For 'weekly_40' OT, we compute base here; weekly overtime is adjusted later in aggregate.
    For 'daily_8', anything over 8 hours in a single shift is OT.
    """
    base_rate = job["base_rate"]
    diff = calc_differential(base_rate, job["diff_type"], job["diff_value"])
    rate_with_diff = base_rate + diff

    hours = row["hours"]
    if job["ot_rule"] == "daily_8":
        regular_hours = min(8.0, hours)
        ot_hours = max(0.0, hours - 8.0)
        earnings = regular_hours * rate_with_diff + ot_hours * (rate_with_diff * job["ot_multiplier"])
        return earnings, regular_hours, ot_hours
    else:
        # weekly rule handled later; treat all as regular for now
        return hours * rate_with_diff, hours, 0.0

def compute_financials(settings):
    jobs = list_jobs(active_only=True).set_index("id")
    shifts = fetch_df("SELECT * FROM shifts ORDER BY shift_date ASC;")
    expenses = fetch_df("SELECT * FROM expenses ORDER BY expense_date ASC;")

    if shifts.empty:
        shifts["shift_date"] = pd.to_datetime([])
    else:
        shifts["shift_date"] = pd.to_datetime(shifts["shift_date"])

    if not shifts.empty:
        # attach job info
        shifts = shifts.merge(jobs.reset_index(), left_on="job_id", right_on="id", suffixes=("","_job"))
        # per-shift earnings
        earnings_list = []
        daily_reg = []
        daily_ot = []
        for _, r in shifts.iterrows():
            e, rhrs, othrs = calc_shift_earnings(r, {
                "base_rate": r["base_rate"],
                "ot_rule": r["ot_rule"],
                "ot_multiplier": r["ot_multiplier"],
                "diff_type": r["diff_type"],
                "diff_value": r["diff_value"]
            })
            earnings_list.append(e)
            daily_reg.append(rhrs)
            daily_ot.append(othrs)
        shifts["earnings_initial"] = earnings_list
        shifts["daily_regular_hours"] = daily_reg
        shifts["daily_ot_hours"] = daily_ot

        # weekly overtime adjustment for 'weekly_40'
        shifts["week"] = shifts["shift_date"].dt.isocalendar().week
        shifts["year"] = shifts["shift_date"].dt.isocalendar().year
        adjusted_earnings = []
        for (job_id, year, week), group in shifts.groupby(["job_id", "year", "week"]):
            job = jobs.loc[job_id]
            if job["ot_rule"] != "weekly_40":
                adjusted_earnings.extend(group["earnings_initial"].tolist())
                continue
            base_rate = job["base_rate"]
            diff = calc_differential(base_rate, job["diff_type"], job["diff_value"])
            rate_with_diff = base_rate + diff
            ot_mult = job["ot_multiplier"]

            total_hours = group["hours"].sum()
            if total_hours <= 40:
                adjusted_earnings.extend(group["hours"] * rate_with_diff)
            else:
                reg_left = 40.0
                job_adj = []
                for _, row in group.iterrows():
                    h = row["hours"]
                    reg = min(reg_left, h)
                    ot = max(0.0, h - reg)
                    reg_left -= reg
                    pay = reg * rate_with_diff + ot * (rate_with_diff * ot_mult)
                    job_adj.append(pay)
                adjusted_earnings.extend(job_adj)
        shifts["earnings"] = adjusted_earnings
    else:
        shifts["earnings"] = 0.0

    if not expenses.empty:
        expenses["expense_date"] = pd.to_datetime(expenses["expense_date"])

    # Weekly view
    if not shifts.empty:
        shifts["week_start"] = shifts["shift_date"].dt.to_period('W-MON').apply(lambda r: r.start_time)
    if not expenses.empty:
        expenses["week_start"] = expenses["expense_date"].dt.to_period('W-MON').apply(lambda r: r.start_time)

    weekly_earnings = shifts.groupby("week_start")["earnings"].sum().reset_index() if not shifts.empty else pd.DataFrame(columns=["week_start","earnings"])
    weekly_expenses = expenses.groupby("week_start")["amount"].sum().reset_index() if not expenses.empty else pd.DataFrame(columns=["week_start","amount"])

    # Current week summary
    today = pd.Timestamp(date.today())
    current_week_start = today.to_period('W-MON').start_time
    earnings_this_week = weekly_earnings.loc[weekly_earnings["week_start"]==current_week_start, "earnings"].sum()
    expenses_this_week = weekly_expenses.loc[weekly_expenses["week_start"]==current_week_start, "amount"].sum()
    net_this_week = earnings_this_week - expenses_this_week

    # Annual projection
    all_weeks = pd.merge(weekly_earnings, weekly_expenses, how="outer", on="week_start").fillna(0.0)
    if len(all_weeks) > 0:
        avg_weekly_net = (all_weeks["earnings"] - all_weeks["amount"]).mean()
    else:
        avg_weekly_net = 0.0
    projected_annual_net = avg_weekly_net * 52.0

    return {
        "jobs": jobs.reset_index(),
        "shifts": shifts,
        "expenses": expenses,
        "weekly_earnings": weekly_earnings,
        "weekly_expenses": weekly_expenses,
        "earnings_this_week": earnings_this_week,
        "expenses_this_week": expenses_this_week,
        "net_this_week": net_this_week,
        "projected_annual_net": projected_annual_net
    }

# ---------------------- UI ----------------------
def page_dashboard(settings):
    st.title("ShiftSavvy — Dashboard")

    data = compute_financials(settings)
    cur = settings["currency"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Earnings (this week)", f"{cur}{data['earnings_this_week']:.2f}")
    c2.metric("Expenses (this week)", f"{cur}{data['expenses_this_week']:.2f}")
    c3.metric("Net (this week)", f"{cur}{data['net_this_week']:.2f}")
    if settings["show_annual"]:
        c4.metric("Projected Annual Net", f"{cur}{data['projected_annual_net']:.0f}")
    else:
        c4.metric("Projected Annual Net", "—")

    st.subheader("Weekly Earnings vs Expenses")
    fig = plt.figure()
    weeks = pd.date_range(end=date.today(), periods=8, freq="W-MON")
    df_plot = pd.DataFrame({"week_start": weeks})
    df_plot = df_plot.merge(data["weekly_earnings"], on="week_start", how="left").merge(
        data["weekly_expenses"], on="week_start", how="left"
    ).fillna(0.0)
    df_plot = df_plot.sort_values("week_start")

    plt.plot(df_plot["week_start"], df_plot["earnings"], label="Earnings")
    plt.plot(df_plot["week_start"], df_plot["amount"], label="Expenses")
    plt.legend()
    plt.xlabel("Week")
    plt.ylabel(f"Amount ({settings['currency']})")
    st.pyplot(fig)

    st.divider()
    st.subheader("Quick Add")
    with st.form("quick_shift"):
        jobs = list_jobs(True)
        if jobs.empty:
            st.info("Add a job in Settings first.")
        else:
            job_map = {f"{row['name']} (${row['base_rate']}/hr)": row["id"] for _, row in jobs.iterrows()}
            selected = st.selectbox("Job", list(job_map.keys()))
            d = st.date_input("Date", date.today())
            hrs = st.number_input("Hours", min_value=0.0, max_value=24.0, value=12.0, step=0.5)
            kind = st.selectbox("Shift type", ["Day","Night","Weekend","Holiday"])
            submitted = st.form_submit_button("Add Shift")
            if submitted:
                add_shift(job_map[selected], d.isoformat(), float(hrs), kind)
                st.success("Shift added.")
                st.experimental_rerun()

    with st.form("quick_expense"):
        cat = st.text_input("Category", "Food")
        amt = st.number_input("Amount", min_value=0.0, value=25.0, step=1.0)
        d2 = st.date_input("Date", date.today(), key="exp_date")
        rec = st.checkbox("Recurring? (note only)", value=False)
        submitted2 = st.form_submit_button("Add Expense")
        if submitted2:
            add_expense(cat, float(amt), d2.isoformat(), rec)
            st.success("Expense added.")
            st.experimental_rerun()

def page_shifts(settings):
    st.title("Shifts")
    data = fetch_df("""
    SELECT s.id, j.name as job, s.shift_date, s.hours, s.shift_kind
    FROM shifts s JOIN jobs j ON s.job_id=j.id
    ORDER BY s.shift_date DESC
    """)
    st.dataframe(data, use_container_width=True)

def page_expenses(settings):
    st.title("Expenses")
    data = fetch_df("""
    SELECT id, category, amount, expense_date, CASE WHEN recurring=1 THEN 'Yes' ELSE 'No' END as recurring
    FROM expenses ORDER BY expense_date DESC
    """)
    st.dataframe(data, use_container_width=True)

def page_export(settings):
    st.title("Data Export")
    shifts = fetch_df("""
    SELECT s.id, j.name as job, s.shift_date, s.hours, s.shift_kind
    FROM shifts s JOIN jobs j ON s.job_id=j.id
    ORDER BY s.shift_date DESC
    """)
    expenses = fetch_df("""
    SELECT id, category, amount, expense_date, recurring
    FROM expenses ORDER BY expense_date DESC
    """)

    st.subheader("Shifts CSV")
    st.download_button("Download shifts.csv", shifts.to_csv(index=False).encode("utf-8"), file_name="shifts.csv", mime="text/csv")

    st.subheader("Expenses CSV")
    st.download_button("Download expenses.csv", expenses.to_csv(index=False).encode("utf-8"), file_name="expenses.csv", mime="text/csv")

def page_settings(settings):
    st.title("Settings")
    st.subheader("Display")
    currency = st.text_input("Currency symbol", value=settings["currency"])
    show_annual = st.checkbox("Show projected annual net", value=settings["show_annual"])
    week_monday = st.checkbox("Week starts Monday (display only)", value=settings["week_monday"])
    if st.button("Save Display Settings"):
        update_settings(currency, show_annual, week_monday)
        st.success("Saved.")

    st.divider()
    st.subheader("Jobs & Pay Rules")
    jobs = list_jobs(active_only=False)
    with st.expander("Add new job"):
        name = st.text_input("Job name", key="newjob_name", value="RN - Travel")
        rate = st.number_input("Base hourly rate", min_value=0.0, value=70.0, step=0.5, key="newjob_rate")
        ot_rule = st.selectbox("OT rule", ["weekly_40", "daily_8"], key="newjob_otr")
        ot_mult = st.number_input("OT multiplier", min_value=1.0, value=1.5, step=0.1, key="newjob_otm")
        diff_type = st.selectbox("Shift differential type", ["percent","fixed"], key="newjob_dt")
        diff_value = st.number_input("Shift differential value", min_value=0.0, value=10.0, step=0.5, key="newjob_dv")
        if st.button("Create Job", key="create_job"):
            add_job(name, float(rate), ot_rule, float(ot_mult), diff_type, float(diff_value), True)
            st.success("Job created.")
            st.experimental_rerun()

    if not jobs.empty:
        for _, row in jobs.iterrows():
            with st.expander(f"Edit: {row['name']} (id {row['id']})", expanded=False):
                name = st.text_input("Job name", value=row["name"], key=f"edit_name_{row['id']}")
                rate = st.number_input("Base hourly rate", min_value=0.0, value=float(row["base_rate"]), step=0.5, key=f"edit_rate_{row['id']}")
                ot_rule = st.selectbox("OT rule", ["weekly_40","daily_8"], index=0 if row["ot_rule"]=="weekly_40" else 1, key=f"edit_otr_{row['id']}")
                ot_mult = st.number_input("OT multiplier", min_value=1.0, value=float(row["ot_multiplier"]), step=0.1, key=f"edit_otm_{row['id']}")
                diff_type = st.selectbox("Shift differential type", ["percent","fixed"], index=0 if row["diff_type"]=="percent" else 1, key=f"edit_dt_{row['id']}")
                diff_value = st.number_input("Shift differential value", min_value=0.0, value=float(row["diff_value"]), step=0.5, key=f"edit_dv_{row['id']}")
                active = st.checkbox("Active", value=bool(row["active"]), key=f"edit_active_{row['id']}")
                c1, c2, c3 = st.columns(3)
                if c1.button("Save", key=f"save_{row['id']}"):
                    update_job(int(row['id']), name, float(rate), ot_rule, float(ot_mult), diff_type, float(diff_value), active)
                    st.success("Saved.")
                    st.experimental_rerun()
                if c2.button("Delete", key=f"del_{row['id']}"):
                    delete_job(int(row['id']))
                    st.warning("Deleted job.")
                    st.experimental_rerun()

# ---------------------- Main ----------------------
def main():
    st.set_page_config(page_title="ShiftSavvy POC", layout="wide")
    init_db()
    settings = get_settings()

    pages = {
        "🏠 Dashboard": page_dashboard,
        "🧾 Shifts": page_shifts,
        "💳 Expenses": page_expenses,
        "📤 Export": page_export,
        "⚙️ Settings": page_settings,
    }
    choice = st.sidebar.radio("Navigate", list(pages.keys()))
    pages[choice](settings)

if __name__ == "__main__":
    main()
