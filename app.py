import datetime
from dateutil import rrule
import mimetypes
import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Pro Task Tracker (Online)", page_icon="🗂️", layout="wide")
st.title("🗂️ Pro Task Tracker (Online)")

# ---- Supabase from secrets ----
def get_supabase():
    try:
        url  = st.secrets["SUPABASE_URL"]
        key  = st.secrets["SUPABASE_KEY"]
        bucket = st.secrets.get("SUPABASE_BUCKET", "task-files")
    except KeyError:
        st.error("Secrets missing. Fill .streamlit/secrets.toml with SUPABASE_URL / SUPABASE_KEY / SUPABASE_BUCKET.")
        st.stop()
    return create_client(url, key), bucket

supabase, BUCKET = get_supabase()

def fail_if_error(resp, msg):
    if getattr(resp, "error", None):
        st.error(f"{msg}: {resp.error.message if hasattr(resp.error,'message') else resp.error}")
        st.stop()
    if isinstance(resp, dict) and resp.get("error"):
        st.error(f"{msg}: {resp['error']}")
        st.stop()

# ===== Create Task =====
with st.sidebar.form("create_task", clear_on_submit=True):
    st.header("Create Task")
    tname = st.text_input("Task name*", placeholder="e.g., Draft report")
    c1, c2 = st.columns(2)
    with c1:
        sdate = st.date_input("Start date*", value=datetime.date.today())
    with c2:
        edate = st.date_input("Deadline*", value=datetime.date.today())
    if st.form_submit_button("➕ Create"):
        if not tname.strip():
            st.sidebar.error("Task name is required.")
        elif edate < sdate:
            st.sidebar.error("Deadline cannot be before start date.")
        else:
            ins = supabase.table("tasks").insert({
                "task_name": tname.strip(),
                "start_date": str(sdate),
                "end_date": str(edate)
            }).execute()
            fail_if_error(ins, "Failed to create task")
            task = (ins.data or [None])[0]
            if not task:
                st.sidebar.error("Task was not created; check policies.")
                st.stop()
            task_id = task["id"]

            # create one log row per day (inclusive)
            for dt in rrule.rrule(rrule.DAILY, dtstart=sdate, until=edate):
                up = supabase.table("logs").upsert({
                    "task_id": task_id,
                    "log_date": str(dt.date()),
                    "progress": "",
                    "percent": 0
                }, on_conflict="task_id,log_date").execute()
                fail_if_error(up, "Failed to create daily rows")

            st.sidebar.success(f"Task created (ID {task_id}).")
            st.rerun()

# ===== List Tasks =====
sel = supabase.table("tasks").select("*").order("updated_at", desc=True).execute()
fail_if_error(sel, "Failed to load tasks")
tasks = sel.data or []
if not tasks:
    st.info("No tasks yet. Create one from the sidebar.")
    st.stop()

df = pd.DataFrame(tasks)
df["Days"] = (pd.to_datetime(df["end_date"]) - pd.to_datetime(df["start_date"])).dt.days + 1
done_pct = []
for t in tasks:
    logs = supabase.table("logs").select("percent").eq("task_id", t["id"]).execute()
    fail_if_error(logs, "Failed to read logs")
    rows = logs.data or []
    avg = round(sum([x.get("percent", 0) for x in rows]) / len(rows)) if rows else 0
    done_pct.append(avg)
df["Done%"] = done_pct
df = df.rename(columns={"id":"ID","task_name":"Task","start_date":"Start","end_date":"End"})

st.subheader("Your Tasks")
st.dataframe(df[["ID","Task","Start","End","Days","Done%"]], use_container_width=True, hide_index=True)

# ===== Open a Task =====
options = {f'#{row["ID"]} — {row["Task"]}': int(row["ID"]) for _, row in df.iterrows()}
label = st.selectbox("Open task", list(options.keys()))
task_id = options[label]
task = [t for t in tasks if t["id"] == task_id][0]
sd = datetime.date.fromisoformat(task["start_date"])
ed = datetime.date.fromisoformat(task["end_date"])

tabs = st.tabs(["Overview", "Daily Update", "Documents"])

# ---------- OVERVIEW ----------
with tabs[0]:
    st.markdown(f"### {task['task_name']}")
    st.caption(f"From **{sd}** to **{ed}**")

    # --- Edit Task Name ---
    with st.expander("✏️ Edit task name"):
        new_name = st.text_input("Task name", value=task["task_name"], key=f"edit_name_{task_id}")
        col_e1, col_e2 = st.columns(2)
        if col_e1.button("Save Changes", key=f"save_name_{task_id}"):
            if not new_name.strip():
                st.error("Task name cannot be empty.")
            else:
                res = supabase.table("tasks").update({"task_name": new_name.strip()}).eq("id", task_id).execute()
                fail_if_error(res, "Failed to update task name")
                st.success("Task name updated!")
                st.rerun()
        if col_e2.button("Cancel", key=f"cancel_name_{task_id}"):
            st.info("No changes saved.")

    # Show all logs
    q = supabase.table("logs").select("*").eq("task_id", task_id).order("log_date").execute()
    fail_if_error(q, "Failed to load logs")
    logs = q.data or []
    if logs:
        dfl = pd.DataFrame(logs).rename(columns={"log_date":"Date","percent":"%","progress":"Notes"})
        st.dataframe(dfl[["Date","%","Notes"]], use_container_width=True, hide_index=True)
    else:
        st.caption("No daily rows yet.")

# ---------- DAILY UPDATE ----------
with tabs[1]:
    st.markdown("### Update a day")
    pick = st.date_input("Date", value=min(max(datetime.date.today(), sd), ed), min_value=sd, max_value=ed)

    # ensure row exists WITHOUT resetting saved values
    supabase.table("logs").upsert(
        {"task_id": task_id, "log_date": str(pick)},
        on_conflict="task_id,log_date"
    ).execute()

    # load the row
    one = supabase.table("logs").select("*").eq("task_id", task_id).eq("log_date", str(pick)).single().execute()
    fail_if_error(one, "Failed to read selected log")
    row = one.data
    if not row:
        st.error("Could not read the daily row. Check policies.")
        st.stop()

    notes = st.text_area("What did you do?", value=row.get("progress",""), height=140, key=f"notes_{task_id}_{pick}")
    pct = st.slider("Completion for this date", 0, 100, int(row.get("percent",0)), step=5, key=f"pct_{task_id}_{pick}")
    cA, cB = st.columns(2)
    if cA.button("✅ Save progress", key=f"save_{task_id}_{pick}"):
        upd = supabase.table("logs").update({"progress": notes, "percent": pct}).eq("id", row["id"]).execute()
        fail_if_error(upd, "Failed to save progress")
        # bubble task to top
        supabase.table("tasks").update({"updated_at": "now()"}).eq("id", task_id).execute()
        st.success("Saved.")
        st.rerun()

    if cB.button("🏁 Mark 100%", key=f"done_{task_id}_{pick}"):
        upd = supabase.table("logs").update({"progress": notes, "percent": 100}).eq("id", row["id"]).execute()
        fail_if_error(upd, "Failed to mark 100%")
        supabase.table("tasks").update({"updated_at": "now()"}).eq("id", task_id).execute()
        st.success("Marked 100%.")
        st.rerun()

    st.markdown("---")
    st.markdown("#### Upload supporting documents for this date")
    files = st.file_uploader("Upload (multiple allowed)", accept_multiple_files=True, key=f"files_{task_id}_{pick}")
    if files:
        uploaded = 0
        for f in files:
            key = f"task_{task_id}/{pick}/{f.name}"
            ctype, _ = mimetypes.guess_type(f.name)
            if ctype is None:
                ctype = "application/octet-stream"
            # overwrite if exists
            try:
                supabase.storage.from_(BUCKET).upload(
                    key,
                    f.getvalue(),
                    {"upsert": True, "contentType": ctype}
                )
            except Exception:
                supabase.storage.from_(BUCKET).update(key, f.getvalue(), {"contentType": ctype})

            # record in docs (avoid dup rows)
            supabase.table("docs").upsert(
                {"log_id": row["id"], "filename": f.name, "path": key},
                on_conflict="log_id,path"
            ).execute()
            uploaded += 1
        st.success(f"Uploaded {uploaded} file(s).")
        st.rerun()

# ---------- DOCUMENTS ----------
with tabs[2]:
    st.markdown("### Documents for this task")
    all_logs = supabase.table("logs").select("id,log_date").eq("task_id", task_id).order("log_date").execute()
    fail_if_error(all_logs, "Failed to load logs for docs")
    for lg in all_logs.data or []:
        docs = supabase.table("docs").select("*").eq("log_id", lg["id"]).order("uploaded_at", desc=True).execute()
        fail_if_error(docs, "Failed to load docs")
        items = docs.data or []
        if not items:
            continue

        st.write(f"**{lg['log_date']}**")
        for d in items:
            cols = st.columns([3, 1, 1])
            # Download button
            try:
                file_bytes = supabase.storage.from_(BUCKET).download(d["path"])
                cols[0].download_button(
                    label=f"Download {d['filename']}",
                    data=file_bytes,
                    file_name=d["filename"],
                    key=f"dl_{d['id']}"
                )
            except Exception:
                url = supabase.storage.from_(BUCKET).get_public_url(d["path"])
                cols[0].markdown(f"[📥 {d['filename']}]({url})")

            # Delete button (removes from storage + DB)
            if cols[1].button("🗑️ Delete", key=f"del_{d['id']}"):
                try:
                    supabase.storage.from_(BUCKET).remove([d["path"]])
                except Exception as e:
                    st.error(f"Storage delete failed: {e}")
                supabase.table("docs").delete().eq("id", d["id"]).execute()
                st.success("File deleted.")
                st.rerun()

            # Small spacer / info
            cols[2].write("")
