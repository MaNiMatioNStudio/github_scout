"""GitHub Startup Scout — Web Dashboard (Streamlit)

Usage:
    streamlit run viewer.py
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
import yaml

OUTPUT_DIR = Path("output")
PREFERENCES_PATH = Path("config/preferences.yaml")

st.set_page_config(page_title="GitHub Startup Scout", page_icon="🔭", layout="wide")


# ── Data helpers ────────────────────────────────────────────────────────────────

@st.cache_data
def load_jsonl(path: str, mtime: float = 0) -> pd.DataFrame:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ("layer1_reasons", "layer2_reasons", "layer3_reasons"):
        if col not in df.columns:
            df[col] = [[] for _ in range(len(df))]
        df[col + "_str"] = df[col].apply(
            lambda v: "\n".join(str(x) for x in v) if isinstance(v, list) else str(v or "")
        )
    # 旧スキーマ(layer2_5_result)→新スキーマ(layer3_result)の互換処理
    if "layer3_result" not in df.columns and "layer2_5_result" in df.columns:
        df["layer3_result"] = df["layer2_5_result"]
    if "layer3_score" not in df.columns and "layer2_5_score" in df.columns:
        df["layer3_score"] = df["layer2_5_score"]
    if "layer3_reasons" not in df.columns and "layer2_5_reasons" in df.columns:
        df["layer3_reasons"] = df["layer2_5_reasons"]
        df["layer3_reasons_str"] = df["layer2_5_reasons"].apply(
            lambda v: "\n".join(str(x) for x in v) if isinstance(v, list) else str(v or "")
        )

    for col, default in [
        ("homepage", ""), ("site_url", ""),
        ("layer3_result", ""), ("layer3_score", 0),
        ("layer5_pass", False), ("layer5_confidence", 0.0),
        ("layer5_lp_url", ""), ("layer5_company_name", ""),
        ("layer5_founder_name", ""),
    ]:
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].fillna(default if not isinstance(default, bool) else False)
    return df


def load_preferences() -> dict:
    if PREFERENCES_PATH.exists():
        with open(PREFERENCES_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_feedback(name: str, url: str, verdict: str, reason: str) -> None:
    prefs = load_preferences()
    judgments = prefs.setdefault("investor_profile", {}).setdefault("past_judgments", [])
    if not any(j.get("name") == name for j in judgments):
        judgments.append({"name": name, "url": url, "verdict": verdict, "reason": reason})
    with open(PREFERENCES_PATH, "w", encoding="utf-8") as f:
        yaml.dump(prefs, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    st.cache_data.clear()


def _s(val) -> str:
    return str(val) if val and str(val) not in ("nan", "None", "") else ""


# ── Process monitor (runs on every render) ──────────────────────────────────────

def _check_running_process() -> None:
    proc = st.session_state.get("runner_proc")
    if not proc:
        return
    rc = proc.poll()
    if rc is None:
        return  # still running

    _lf = st.session_state.get("runner_log_f")
    if _lf:
        _lf.close()
        st.session_state["runner_log_f"] = None

    _tag = st.session_state.get("runner_tag", "")

    if rc == 0:
        _new_file = str(OUTPUT_DIR / f"filtered_repos_{_tag}.jsonl")
        st.session_state["runner_proc"]      = None
        st.session_state["runner_active"]    = False
        st.session_state["auto_select_file"] = _new_file
        st.cache_data.clear()
        st.rerun()
    else:
        st.session_state["runner_proc"]   = None
        st.session_state["runner_active"] = False
        st.rerun()


_check_running_process()


# ── Components ──────────────────────────────────────────────────────────────────

def render_repo_card(row) -> None:
    with st.container(border=True):
        col_info, col_action = st.columns([3, 1])
        with col_info:
            st.markdown(f"### [{row['name']}]({row['url']})")
            lang   = row.get("language") or "?"
            stars  = row.get("stars", 0)
            score  = row.get("score", 0.0)
            l3_res = _s(row.get("layer3_result"))
            l3_scr = row.get("layer3_score", 0)
            st.caption(f"`{lang}` · ⭐{stars} · Score {score:.2f} · 🇯🇵 L3:{l3_res} ({l3_scr}pt)")

            company = _s(row.get("layer5_company_name"))
            founder = _s(row.get("layer5_founder_name"))
            lp_url  = _s(row.get("layer5_lp_url"))
            try:
                l5_conf_f = float(row.get("layer5_confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                l5_conf_f = 0.0
            l5_pass = row.get("layer5_pass", False)
            l5_done = l5_conf_f > 0.0

            if l5_done:
                with st.container(border=True):
                    r1, r2 = st.columns(2)
                    with r1:
                        st.markdown("**🏢 企業名**")
                        st.markdown(company if company else "—")
                        st.markdown("**👤 代表者**")
                        st.markdown(founder if founder else "—")
                    with r2:
                        st.markdown("**🌐 サービスURL**")
                        if lp_url:
                            st.markdown(f"[{lp_url}]({lp_url})")
                        else:
                            st.markdown("—")
                        st.markdown("**🇯🇵 日本向け判定**")
                        if l5_pass:
                            st.markdown(f"✅ 日本向け ({l5_conf_f:.0%})")
                        else:
                            st.markdown(f"❌ 対象外 ({l5_conf_f:.0%})")

            if row.get("description"):
                st.markdown(f"*{row['description']}*")

        with col_action:
            st.link_button("🔗 GitHub", row["url"], use_container_width=True)
            site_url = lp_url or _s(row.get("site_url"))
            if site_url:
                st.link_button("🌐 サイト", site_url, use_container_width=True)


def render_log_table(df: pd.DataFrame, stage_key: str) -> None:
    fc1, fc2 = st.columns([1, 3])
    with fc1:
        langs = ["(all)"] + sorted(df["language"].dropna().unique().tolist()) if not df.empty else ["(all)"]
        filter_lang = st.selectbox("言語", langs, key=f"lang_{stage_key}")
    with fc2:
        filter_kw = st.text_input("キーワード", key=f"kw_{stage_key}")

    filtered = df.copy()
    if filter_lang != "(all)":
        filtered = filtered[filtered["language"] == filter_lang]
    if filter_kw:
        kw = filter_kw.lower()
        filtered = filtered[
            filtered["name"].str.lower().str.contains(kw, na=False)
            | filtered["description"].str.lower().str.contains(kw, na=False)
        ]
    filtered = filtered.sort_values("score", ascending=False)

    show_cols = [c for c in [
        "name", "owner", "language", "stars", "score",
        "layer1_pass", "layer2_pass", "layer3_result",
        "site_url", "description",
    ] if c in filtered.columns]

    _dl, _cnt = st.columns([1, 5])
    with _cnt:
        st.caption(f"{len(filtered)} 件")
    with _dl:
        _csv = filtered[show_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇ CSV", _csv, file_name=f"{stage_key}.csv",
                           mime="text/csv", use_container_width=True)

    st.dataframe(
        filtered[show_cols],
        use_container_width=True,
        height=420,
        column_config={
            "name":         st.column_config.TextColumn("Repo", width="medium"),
            "owner":        st.column_config.TextColumn("Owner", width="small"),
            "language":     st.column_config.TextColumn("Lang", width="small"),
            "stars":        st.column_config.NumberColumn("★", width="small"),
            "score":        st.column_config.NumberColumn("Score", format="%.2f", width="small"),
            "layer1_pass":  st.column_config.CheckboxColumn("L1", width="small"),
            "layer2_pass":  st.column_config.CheckboxColumn("L2", width="small"),
            "layer3_result": st.column_config.TextColumn("L3", width="small"),
            "site_url":     st.column_config.LinkColumn("サイト", width="medium", display_text="🌐 開く"),
            "description":  st.column_config.TextColumn("Description", width="large"),
        },
        hide_index=True,
    )

    names = filtered["name"].tolist()
    if names:
        with st.expander("詳細"):
            sel = st.selectbox("Repo を選択", names, key=f"detail_{stage_key}")
            row = filtered[filtered["name"] == sel].iloc[0]
            cl, cr = st.columns(2)
            with cl:
                st.markdown(f"**[{row['name']}]({row['url']})**")
                st.markdown(f"`{row.get('language','—')}` / ⭐{row.get('stars',0)} / Score {row.get('score',0):.2f}")
                st.markdown(f"*{row.get('description','')}*")
                st.link_button("GitHub で開く", row["url"])
            with cr:
                for col_str, label in [
                    ("layer1_reasons_str", "Layer1"),
                    ("layer2_reasons_str", "Layer2"),
                    ("layer3_reasons_str", "Layer3"),
                ]:
                    val = row.get(col_str, "")
                    if val:
                        st.markdown(f"**{label}**")
                        st.code(val, language=None)


# ── Pages ───────────────────────────────────────────────────────────────────────

def page_extract() -> None:
    st.title("📥 新規抽出")

    # Status / log
    is_running = st.session_state.get("runner_active", False)
    if is_running:
        st.info("⏳ 実行中…")
    if "runner_log_path" in st.session_state:
        _log_path = Path(st.session_state["runner_log_path"])
        if _log_path.exists():
            _log_text = _log_path.read_text(encoding="utf-8", errors="ignore")
            with st.expander("📋 実行ログ", expanded=is_running):
                st.text(_log_text[-8000:])
        if is_running:
            time.sleep(2)
            st.rerun()

    st.divider()

    # Date input (1日固定)
    yesterday = (datetime.now() - timedelta(days=1)).date()
    run_from = st.date_input("対象日 (JST)", value=yesterday, key="run_from")
    run_to   = run_from  # 1日のみ

    run_time_from = None
    run_time_to   = None
    with st.expander("⏱ 時刻絞り込み"):
        st.caption("JST基準で入力。内部でUTCに変換します。")
        _tc1, _tc2 = st.columns(2)
        with _tc1:
            run_time_from = st.time_input("開始時刻 (JST)", value=None, key="run_time_from", step=60)
        with _tc2:
            run_time_to   = st.time_input("終了時刻 (JST)", value=None, key="run_time_to",   step=60)
        if run_time_from and run_time_to and run_time_from >= run_time_to:
            st.warning("終了時刻 > 開始時刻にしてください")
        if run_time_from and run_time_to and run_time_from < run_time_to:
            from datetime import datetime as _dt
            _utc_from = _dt.combine(run_from, run_time_from) - timedelta(hours=9)
            _utc_to   = _dt.combine(run_to,   run_time_to)   - timedelta(hours=9)
            st.caption(f"UTC換算: {_utc_from.strftime('%Y-%m-%d %H:%M')} 〜 {_utc_to.strftime('%Y-%m-%d %H:%M')}")
    use_time_range = (run_time_from is not None and run_time_to is not None
                      and run_time_from < run_time_to)

    run_name_filter = st.text_input(
        "🔤 repo名フィルター（部分一致、空白=全件）",
        value="", key="run_name_filter",
        placeholder="例: zeimee",
    )

    # Build UTC args
    if use_time_range:
        from datetime import datetime as _dt
        _utc_from_dt   = _dt.combine(run_from, run_time_from) - timedelta(hours=9)
        _utc_to_dt     = _dt.combine(run_to,   run_time_to)   - timedelta(hours=9)
        _cmd_date_from = str(_utc_from_dt.date())
        _cmd_date_to   = str(_utc_to_dt.date())
        _cmd_time_from = _utc_from_dt.strftime("%H:%M:%S")
        _cmd_time_to   = _utc_to_dt.strftime("%H:%M:%S")
    else:
        _cmd_date_from = str(run_from)
        _cmd_date_to   = str(run_to)

    _prev_cmd = ["python", "main.py",
                 "--date-from", _cmd_date_from, "--date-to", _cmd_date_to,
                 "--yes"]
    if use_time_range:
        _prev_cmd += ["--time-from", _cmd_time_from, "--time-to", _cmd_time_to]
    if run_name_filter.strip():
        _prev_cmd += ["--name-contains", run_name_filter.strip()]
    with st.expander("コマンド確認 (UTC)"):
        st.code(" ".join(_prev_cmd), language="bash")

    _rb1, _rb2 = st.columns(2)
    with _rb1:
        run_clicked  = st.button("▶ 実行",  type="primary",    disabled=is_running, use_container_width=True)
    with _rb2:
        stop_clicked = st.button("⏹ 中断", disabled=not is_running, use_container_width=True)

    if run_clicked:
        if use_time_range:
            tf  = run_time_from.strftime("%H%M%S")
            tt  = run_time_to.strftime("%H%M%S")
            tag = f"{run_from}T{tf}-{tt}JST"
        elif run_from == run_to:
            tag = str(run_from)
        else:
            tag = f"{run_from}_{run_to}"

        _run_ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = OUTPUT_DIR / f"run_{tag}_{_run_ts}.log"
        cmd = [sys.executable, str(Path(__file__).parent / "main.py"),
               "--date-from", _cmd_date_from, "--date-to", _cmd_date_to,
               "--yes"]
        if use_time_range:
            cmd += ["--time-from", _cmd_time_from, "--time-to", _cmd_time_to]
        if run_name_filter.strip():
            cmd += ["--name-contains", run_name_filter.strip()]

        OUTPUT_DIR.mkdir(exist_ok=True)
        log_f = open(log_path, "w", encoding="utf-8")
        proc  = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT,
                                 cwd=str(Path(__file__).parent))
        st.session_state.update({
            "runner_active":   True,
            "runner_proc":     proc,
            "runner_log_f":    log_f,
            "runner_log_path": str(log_path),
            "runner_tag":      tag,
        })
        st.rerun()

    if stop_clicked:
        proc = st.session_state.get("runner_proc")
        if proc:
            import signal, os as _os
            try:
                _os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log_f = st.session_state.get("runner_log_f")
        if log_f: log_f.close()
        st.session_state["runner_proc"]   = None
        st.session_state["runner_active"] = False
        st.rerun()


def page_all_files() -> None:
    st.title("📂 全抽出結果一覧")

    jsonl_files = sorted(
        OUTPUT_DIR.glob("filtered_repos_*.jsonl"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    ) if OUTPUT_DIR.exists() else []

    if not jsonl_files:
        st.info("抽出結果がありません。")
        return

    for f in jsonl_files:
        mtime    = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        size_kb  = f.stat().st_size / 1024
        label    = f.stem.replace("filtered_repos_", "")
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            st.write(label)
        with c2:
            st.caption(f"{mtime} · {size_kb:.0f} KB")
        with c3:
            if st.button("開く", key=f"open_{f.name}", use_container_width=True):
                st.session_state["selected_file"] = str(f)
                st.session_state["page"] = "results"
                st.session_state.pop("funnel_filter", None)
                st.rerun()


def page_prefs() -> None:
    st.title("⚙ 投資観")

    prefs   = load_preferences()
    profile = prefs.get("investor_profile", {})

    st.subheader("プロファイル")
    st.markdown(profile.get("description", "未設定"))

    col_pos, col_neg = st.columns(2)
    with col_pos:
        st.markdown("**✅ ポジティブシグナル**")
        for s in profile.get("positive_signals", []):
            st.markdown(f"- {s}")
    with col_neg:
        st.markdown("**❌ ネガティブシグナル**")
        for s in profile.get("negative_signals", []):
            st.markdown(f"- {s}")

    st.divider()
    st.subheader("フィードバック履歴")
    judgments = profile.get("past_judgments", [])

    if not judgments:
        st.info("まだフィードバックはありません。")
    else:
        for j in reversed(judgments):
            icon   = "✅" if j.get("verdict") == "good" else "❌"
            name   = j.get("name", "")
            url    = j.get("url", "")
            reason = j.get("reason", "")
            st.markdown(f"{icon} **[{name}]({url})** — {reason}")

        st.divider()
        if st.button("⚠ 全フィードバックをリセット", type="secondary"):
            prefs["investor_profile"]["past_judgments"] = []
            with open(PREFERENCES_PATH, "w", encoding="utf-8") as f:
                yaml.dump(prefs, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            st.cache_data.clear()
            st.rerun()


def page_results(selected_file: Optional[str]) -> None:
    if not selected_file or not Path(selected_file).exists():
        st.info("左のサイドバーから抽出結果を選択してください。")
        return

    f      = Path(selected_file)
    df_all = load_jsonl(str(f), float(f.stat().st_mtime))

    if df_all.empty:
        st.warning("選択中のファイルが空です。")
        return

    tag = f.stem.replace("filtered_repos_", "")

    # ── 自動更新（取得中ファイルのライブモニタリング）────────────────────────
    import time as _time
    age_sec = _time.time() - f.stat().st_mtime
    is_live = age_sec < 120  # 2分以内に更新があれば「取得中」とみなす
    if is_live:
        st.title(f"⏳ {tag}  *(取得中)*")
        col_auto, col_interval = st.columns([2, 2])
        with col_auto:
            auto_refresh = st.toggle("自動更新", value=True, key="auto_refresh")
        with col_interval:
            interval = st.selectbox("更新間隔", [10, 30, 60], index=1,
                                    format_func=lambda x: f"{x}秒", key="refresh_interval")
        if auto_refresh:
            st.caption(f"🔄 {interval}秒ごとに自動更新")
            _time.sleep(interval)
            st.cache_data.clear()
            st.rerun()
    else:
        st.title(f"📊 {tag}")

    # ── Funnel ────────────────────────────────────────────────────────────────
    total = len(df_all)
    l1    = int(df_all["layer1_pass"].sum())
    l2    = int(df_all["layer2_pass"].sum())
    l3    = int((df_all["layer3_result"] == "pass").sum()) if "layer3_result" in df_all.columns else 0

    funnel_stages = [
        ("all", "取得",       total, ""),
        ("l1",  "L1通過",     l1,    f"{l1/total*100:.0f}%"  if total else "—"),
        ("l2",  "L2通過",     l2,    f"{l2/l1*100:.0f}%"     if l1    else "—"),
        ("l3",  "🇯🇵 L3通過",  l3,    f"{l3/l2*100:.0f}%"     if l2    else "—"),
    ]

    current_filter = st.session_state.get("funnel_filter")

    cols = st.columns(len(funnel_stages))
    for col, (key, label, count, delta) in zip(cols, funnel_stages):
        with col:
            st.metric(label, count, delta if delta else None)
            is_active  = current_filter == key
            btn_label  = "▶ 表示中" if is_active else "一覧表示"
            if st.button(btn_label, key=f"funnel_{key}",
                         type="primary" if is_active else "secondary",
                         use_container_width=True):
                st.session_state["funnel_filter"] = None if is_active else key
                st.rerun()

    st.divider()

    # ── Content ───────────────────────────────────────────────────────────────
    if current_filter:
        filter_map = {
            "all": (df_all,                                                            "全取得"),
            "l1":  (df_all[df_all["layer1_pass"] == True],                            "L1通過"),
            "l2":  (df_all[df_all["layer2_pass"] == True],                            "L2通過"),
            "l3":  (df_all[df_all["layer3_result"] == "pass"] if "layer3_result" in df_all.columns else df_all.iloc[0:0],
                    "🇯🇵 L3通過"),
        }
        df_show, stage_label = filter_map.get(current_filter, (df_all, ""))
        st.subheader(stage_label)
        render_log_table(df_show, current_filter)
    else:
        # Default: show L3 passed repos as cards
        if "layer3_result" in df_all.columns:
            l3_passed = df_all[df_all["layer3_result"] == "pass"].copy()
        else:
            l3_passed = df_all.iloc[0:0]

        if l3_passed.empty:
            st.info("L3通過リポジトリはありません。ファネルのボタンで各ステージの一覧を確認できます。")
        else:
            prefs      = load_preferences()
            judged_map = {j["name"]: j["verdict"]
                          for j in prefs.get("investor_profile", {}).get("past_judgments", [])}

            pending  = l3_passed[~l3_passed["name"].isin(judged_map.keys())]
            reviewed = l3_passed[l3_passed["name"].isin(judged_map.keys())]

            st.subheader(f"🇯🇵 L3通過 — {len(l3_passed)} 件")
            for _, row in pending.iterrows():
                render_repo_card(row)
            if not reviewed.empty:
                with st.expander(f"フィードバック済み ({len(reviewed)} 件)"):
                    for _, row in reviewed.iterrows():
                        render_repo_card(row)


# ── Sidebar ──────────────────────────────────────────────────────────────────────

jsonl_files = sorted(
    OUTPUT_DIR.glob("filtered_repos_*.jsonl"),
    key=lambda p: p.stat().st_mtime, reverse=True,
) if OUTPUT_DIR.exists() else []

with st.sidebar:
    st.title("🔭 Scout")

    current_page = st.session_state.get("page", "results")

    if st.button("📥 新規抽出",
                 type="primary" if current_page == "extract" else "secondary",
                 use_container_width=True):
        st.session_state["page"] = "extract"
        st.session_state.pop("funnel_filter", None)
        st.rerun()

    if st.button("⚙ 投資観",
                 type="primary" if current_page == "prefs" else "secondary",
                 use_container_width=True):
        st.session_state["page"] = "prefs"
        st.rerun()

    if st.session_state.get("runner_active"):
        st.info("⏳ 実行中…")

    st.divider()
    st.subheader("📊 抽出結果")

    # Auto-select newly created file
    _auto = st.session_state.pop("auto_select_file", None)
    if _auto:
        st.session_state["selected_file"] = _auto
        st.session_state["page"] = "results"

    if not jsonl_files:
        st.caption("まだ結果がありません")
    else:
        recent = jsonl_files[:10]
        for f in recent:
            label       = f.stem.replace("filtered_repos_", "")
            is_selected = (str(f) == st.session_state.get("selected_file", "")
                           and current_page == "results")
            if st.button(label, key=f"file_{f.name}",
                         type="primary" if is_selected else "secondary",
                         use_container_width=True):
                st.session_state["selected_file"] = str(f)
                st.session_state["page"] = "results"
                st.session_state.pop("funnel_filter", None)
                st.rerun()

        if len(jsonl_files) > 10:
            if st.button("それ以降...", use_container_width=True):
                st.session_state["page"] = "all_files"
                st.rerun()


# ── Routing ──────────────────────────────────────────────────────────────────────

page = st.session_state.get("page", "results")

if page == "extract":
    page_extract()
elif page == "all_files":
    page_all_files()
elif page == "prefs":
    page_prefs()
else:
    sel = st.session_state.get("selected_file")
    if not sel and jsonl_files:
        sel = str(jsonl_files[0])
        st.session_state["selected_file"] = sel
    page_results(sel)
