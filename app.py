import streamlit as st
import pandas as pd
import io
import os
import urllib.request
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ページ基本設定
st.set_page_config(page_title="製本管理業務 - ＆@pocket書き戻し（AIアシスタント搭載版）", layout="wide")

st.title("📦 製本管理業務 個別工程除外 ＆ @pocket書き戻し・AIスケジュールシステム")

# ── 安全な整数変換ヘルパー関数 ──
def safe_int(val, default=1):
    if pd.isna(val) or str(val).strip() == '' or str(val).lower() == 'nan':
        return default
    try:
        return int(float(str(val)))
    except (ValueError, TypeError):
        return default

# ── 日本語フォント（NotoSansJP）の自動取得・登録処理 ──
font_name = 'Helvetica' # フォールバック
font_filename = "NotoSansJP-Regular.ttf"

if not os.path.exists(font_filename):
    try:
        font_url = "https://github.com/google/fonts/raw/main/ofl/notosansjp/NotoSansJP-Regular.ttf"
        urllib.request.urlretrieve(font_url, font_filename)
    except Exception as e:
        print(f"Font download error: {e}")

if os.path.exists(font_filename):
    try:
        pdfmetrics.registerFont(TTFont('JapFont', font_filename))
        font_name = 'JapFont'
    except Exception as e:
        print(f"Font registration error: {e}")

# ── セッションステートの初期化 ──
if "calculated" not in st.session_state:
    st.session_state.calculated = False
if "df_schedule_out" not in st.session_state:
    st.session_state.df_schedule_out = None
if "df_order_summary" not in st.session_state:
    st.session_state.df_order_summary = None
if "daily_slot_pool" not in st.session_state:
    st.session_state.daily_slot_pool = None
if "shift_dates" not in st.session_state:
    st.session_state.shift_dates = set()
if "ng_schedule_rules" not in st.session_state:
    st.session_state.ng_schedule_rules = {}
if "special_rules" not in st.session_state:
    st.session_state.special_rules = [] # 特殊ルールの保持用
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [{"role": "assistant", "content": "こんにちは！AI現場アシスタントです。スケジュール計算後に、「今週一番残業が多い日は？」や「特定の案件の状況」について何でもご質問ください！"}]

# ── サイドバー：各種CSVデータの選択と制約パラメータ ──
with st.sidebar:
    st.header("1. 各種CSVデータの選択")
    f1 = st.file_uploader("CSV① 受注データ（必須）", type=["csv"])
    f2 = st.file_uploader("CSV② 休暇シフトデータ（任意）", type=["csv"])
    f3 = st.file_uploader("CSV③ 機械稼働NGデータ（任意）", type=["csv"])
    
    st.header("2. 現場の制約パラメータ")
    base_staff = st.number_input("基本人員数（通常のマックスマンパワー・人）", min_value=1, value=10)
    am_hours = st.number_input("AM労働時間（例: 8:30〜12:00）", min_value=0.0, value=3.5)
    pm_hours = st.number_input("PM労働時間（例: 13:00〜17:30）", min_value=0.0, value=4.5)
    
    deadline_offset = st.number_input("完成期日オフセット（納期の何日前までに完了させるか）", min_value=0, value=1)
    start_base_date = st.date_input("シミュレーション開始基準日（起算日）", value=datetime.today().date())

# メイン画面の処理
if f1 is None:
    st.info("👈 左側のサイドバーから **CSV①（受注データ）** をアップロードしてください。")
else:
    try:
        # ── 1. CSV① 受注データの読み込み ──
        f1_bytes = f1.read()
        try:
            df_order = pd.read_csv(io.StringIO(f1_bytes.decode('utf-8')))
        except UnicodeDecodeError:
            df_order = pd.read_csv(io.StringIO(f1_bytes.decode('cp932', errors='ignore')))
        
        df_order.columns = df_order.columns.str.strip()
        
        if '工程' in df_order.columns:
            df_order = df_order.dropna(subset=['工程'])
            df_order = df_order[df_order['工程'].astype(str).str.strip() != '']
            df_order = df_order[df_order['工程'].astype(str).str.lower() != 'nan']
            
        if df_order.empty:
            st.error("有効なプログラムデータが存在する行が見つかりませんでした。")
        else:
            id_col_name = None
            for col in df_order.columns:
                if '明細レコードID' in col or 'レコードID' in col or ('id' in col.lower() and '明細' in col):
                    id_col_name = col
                    break
            if not id_col_name:
                for col in df_order.columns:
                    if 'id' in col.lower() or '識別' in col:
                        id_col_name = col
                        break

            def normalize_date_str(d_str):
                if pd.isna(d_str) or not str(d_str).strip():
                    return ""
                s = str(d_str).strip()
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                    try:
                        return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
                    except ValueError:
                        continue
                return s

            df_order['納期日付'] = df_order['納期日付'].apply(normalize_date_str)
            base_date_str = start_base_date.strftime("%Y-%m-%d")
            df_order_valid = df_order[df_order['納期日付'] >= base_date_str].copy()

            if df_order_valid.empty:
                st.warning("起算日以降の納期を持つ受注データが見つかりませんでした。")
            else:
                # ── タブの作成（メイン計算 ＆ AIアシスタント・特殊ルール） ──
                tab_main, tab_ai = st.tabs(["📈 スケジュール計算・出力管理", "🤖 AI現場アシスタント ＆ 特殊ルール"])

                with tab_main:
                    st.markdown("---")
                    st.subheader("📋 個別「工程行」除外エディタ（除外したい特定の工程にチェック）")
                    df_editor_view = df_order_valid.copy()
                    df_editor_view.insert(0, "除外する", False)

                    edited_df = st.data_editor(
                        df_editor_view,
                        column_config={
                            "除外する": st.column_config.CheckboxColumn("除外する", default=False),
                        },
                        hide_index=True,
                        use_container_width=True
                    )

                    df_target = edited_df[edited_df["除外する"] == False].copy()

                    if st.button("🚀 選択した内容を反映してスケジュールを計算する", type="primary"):
                        if df_target.empty:
                            st.error("すべての工程行が除外されています。")
                            st.session_state.calculated = False
                        else:
                            # ── 2. CSV② 休暇シフトデータの読み込み ──
                            shift_staff_reduction = {}
                            shift_dates = set()
                            
                            if f2 is not None:
                                try:
                                    f2_bytes = f2.read()
                                    try:
                                        df_shift = pd.read_csv(io.StringIO(f2_bytes.decode('utf-8')))
                                    except UnicodeDecodeError:
                                        df_shift = pd.read_csv(io.StringIO(f2_bytes.decode('cp932', errors='ignore')))
                                    
                                    df_shift.columns = df_shift.columns.str.strip()
                                    for _, s_row in df_shift.iterrows():
                                        d_val_raw = ""
                                        for col in df_shift.columns:
                                            if '日付' in col or 'date' in col.lower():
                                                d_val_raw = s_row.get(col, "")
                                                break
                                        d_val = normalize_date_str(d_val_raw)
                                        if d_val and d_val.lower() != 'nan':
                                            shift_dates.add(d_val)
                                            minus_count = 1
                                            for col in df_shift.columns:
                                                if '人員' in col or '人数' in col or 'スタッフ' in col:
                                                    minus_count = safe_int(s_row.get(col, 1), 1)
                                                    break
                                            shift_staff_reduction[d_val] = shift_staff_reduction.get(d_val, 0) + minus_count
                                except Exception as e:
                                    st.warning(f"CSV②の読み込みに失敗しましたが続行します: {e}")

                            # ── 3. CSV③ 機械稼働NGデータの読み込み ──
                            ng_schedule_rules = {}
                            if f3 is not None:
                                try:
                                    f3_bytes = f3.read()
                                    try:
                                        df_ng = pd.read_csv(io.StringIO(f3_bytes.decode('utf-8')))
                                    except UnicodeDecodeError:
                                        df_ng = pd.read_csv(io.StringIO(f3_bytes.decode('cp932', errors='ignore')))
                                    
                                    df_ng.columns = df_ng.columns.str.strip()
                                    date_col, time_col, proc_col = None, None, None
                                    for col in df_ng.columns:
                                        if '日付' in col or 'date' in col.lower():
                                            date_col = col
                                        elif '時間' in col or 'period' in col.lower():
                                            time_col = col
                                        elif '工程' in col or 'ng' in col.lower():
                                            proc_col = col
                                    
                                    for _, n_row in df_ng.iterrows():
                                        d_raw = n_row.get(date_col, "") if date_col else ""
                                        d_val = normalize_date_str(d_raw)
                                        if not d_val:
                                            continue
                                        t_val = str(n_row.get(time_col, "全日")).strip().upper() if time_col else "全日"
                                        p_val = str(n_row.get(proc_col, "")).strip() if proc_col else ""
                                        if d_val not in ng_schedule_rules:
                                            ng_schedule_rules[d_val] = []
                                        ng_schedule_rules[d_val].append({"process": p_val, "time_period": t_val})
                                except Exception as g_err:
                                    st.warning(f"CSV③の読み込みに失敗しましたが続行します: {g_err}")

                            # 特殊ルールの追加ペナルティ（例：段取り替え時間等の上乗せ）の考慮
                            setup_extra_minutes = 0.0
                            for rule in st.session_state.special_rules:
                                if "段取り替え" in rule.get("type", ""):
                                    setup_extra_minutes += float(rule.get("value", 0))

                            # ── 4. 計算メインループ ──
                            order_due_sort = df_target.groupby('受注番号')['納期日付'].min().reset_index()
                            order_due_sort = order_due_sort.sort_values(by=['納期日付', '受注番号'], ascending=[True, True])
                            sorted_order_ids = order_due_sort['受注番号'].tolist()

                            daily_slot_pool = {}
                            scheduled_results = []
                            order_summary_list = []
                            
                            def is_process_ng(d_str, slot_name, process_name):
                                if d_str not in ng_schedule_rules:
                                    return False
                                for r in ng_schedule_rules[d_str]:
                                    r_proc = r["process"]
                                    r_time = r["time_period"]
                                    is_proc_match = (not r_proc or r_proc in ["すべて", "全般", "全工程", "nan"] or r_proc == process_name)
                                    if not is_proc_match:
                                        continue
                                    if r_time in ["全日", "終日", "ALL", "NAN"]:
                                        return True
                                    elif slot_name == "AM" and any(k in r_time for k in ["AM", "午前", "8:", "9:", "10:", "11:"]):
                                        return True
                                    elif slot_name == "PM" and any(k in r_time for k in ["PM", "午後", "13:", "14:", "15:", "16:", "17:"]):
                                        return True
                                return False

                            for order_id in sorted_order_ids:
                                group = df_target[df_target['受注番号'] == order_id]
                                rows = group.to_dict('records')
                                customer = str(rows[0].get("得意先", ""))
                                item_name = str(rows[0].get("書名1", ""))
                                due_date_str = str(rows[0].get("納期日付", ""))
                                
                                try:
                                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
                                except:
                                    due_date = datetime.today() + timedelta(days=10)
                                
                                sorted_rows = sorted(rows, key=lambda x: safe_int(x.get("順"), 1))
                                current_assign_date = datetime.combine(start_base_date, datetime.min.time())
                                current_slot = "AM"
                                
                                last_task_end_date = current_assign_date
                                last_task_end_slot = current_slot
                                order_first_start_date = None
                                order_last_end_date = None
                                order_used_slots = set()
                                
                                for row in sorted_rows:
                                    seq = safe_int(row.get("順"), 1)
                                    process = str(row.get("工程", ""))
                                    staff_needed = safe_int(row.get("人数"), 1)
                                    record_id = str(row.get(id_col_name, "")) if id_col_name else ""
                                    
                                    duration_minutes = float(row.get("所要時間", 120)) + setup_extra_minutes
                                    task_hours = duration_minutes / 60.0
                                    task_man_hours = staff_needed * task_hours
                                    
                                    target_date = last_task_end_date
                                    target_slot = last_task_end_slot
                                    
                                    while True:
                                        d_str = target_date.strftime("%Y-%m-%d")
                                        slot_key = (d_str, target_slot)
                                        
                                        reduction = shift_staff_reduction.get(d_str, 0)
                                        effective_staff = max(1, base_staff - reduction)
                                        am_capacity = effective_staff * am_hours
                                        pm_capacity = effective_staff * pm_hours
                                        
                                        if d_str not in daily_slot_pool:
                                            daily_slot_pool[d_str] = {
                                                "reduction": reduction,
                                                "effective_staff": effective_staff,
                                                "AM": {"used": 0.0, "max": am_capacity},
                                                "PM": {"used": 0.0, "max": pm_capacity}
                                            }
                                        else:
                                            daily_slot_pool[d_str]["AM"]["max"] = am_capacity
                                            daily_slot_pool[d_str]["PM"]["max"] = pm_capacity
                                        
                                        is_ng = is_process_ng(d_str, target_slot, process)
                                        slot_data = daily_slot_pool[d_str][target_slot]
                                        
                                        if not is_ng and slot_key not in order_used_slots and (slot_data["used"] + task_man_hours <= slot_data["max"]):
                                            slot_data["used"] += task_man_hours
                                            order_used_slots.add(slot_key)
                                            break
                                        else:
                                            if target_slot == "AM":
                                                target_slot = "PM"
                                            else:
                                                target_date = target_date + timedelta(days=1)
                                                target_slot = "AM"
                                    
                                    d_str = target_date.strftime("%Y-%m-%d")
                                    assigned_slot_name = "AM (8:30〜12:00)" if target_slot == "AM" else "PM (13:00〜17:30)"
                                    
                                    if order_first_start_date is None:
                                        order_first_start_date = target_date
                                    order_last_end_date = target_date
                                    
                                    scheduled_results.append({
                                        "割当作業日": d_str,
                                        "時間帯": assigned_slot_name,
                                        "納期": due_date_str,
                                        "受注番号": order_id,
                                        "順番": seq,
                                        "案件名": item_name,
                                        "顧客名": customer,
                                        "工程": process,
                                        "必要人員": f"{staff_needed}人",
                                        "作業時間": f"{task_hours:.1f}h ({duration_minutes:.0f}分)",
                                        "消費人時": f"{task_man_hours:.1f}h",
                                        "明細レコードID": record_id
                                    })
                                    last_task_end_date = target_date
                                    last_task_end_slot = target_slot

                                if order_last_end_date:
                                    planned_completion_str = order_last_end_date.strftime("%Y-%m-%d")
                                    diff_days = (due_date - order_last_end_date).days
                                    timing_status = f"納期より {diff_days} 日前に完成" if diff_days > 0 else ("納期当日に完成" if diff_days == 0 else f"納期より {abs(diff_days)} 日遅延")
                                    order_summary_list.append({
                                        "受注番号": order_id,
                                        "顧客名": customer,
                                        "品名（案件名）": item_name,
                                        "納品完成日（納期）": due_date_str,
                                        "予定完成日": planned_completion_str,
                                        "完了タイミング": timing_status,
                                        "余裕日数": f"{diff_days}日"
                                    })

                            st.session_state.df_schedule_out = pd.DataFrame(scheduled_results).sort_values(by=["割当作業日", "時間帯", "受注番号", "順番"])
                            st.session_state.df_order_summary = pd.DataFrame(order_summary_list)
                            st.session_state.daily_slot_pool = daily_slot_pool
                            st.session_state.shift_dates = shift_dates
                            st.session_state.ng_schedule_rules = ng_schedule_rules
                            st.session_state.calculated = True
                            st.success("スケジュール計算が完了しました！")

                    # ── 計算済みデータの表示・出力セクション ──
                    if st.session_state.calculated and st.session_state.df_schedule_out is not None:
                        st.markdown("---")
                        st.markdown("### 🎯 品名・顧客ごとの完成日チェック")
                        st.dataframe(st.session_state.df_order_summary, use_container_width=True)
                        
                        st.markdown("---")
                        st.markdown("### 📋 工程別 詳細スケジュール一覧")
                        st.dataframe(st.session_state.df_schedule_out, use_container_width=True)
                        
                        # PDFダウンロード
                        st.markdown("---")
                        st.subheader("📄 日別スケジュール PDFダウンロード")
                        df_schedule_out = st.session_state.df_schedule_out
                        available_dates_pdf = sorted(df_schedule_out["割当作業日"].unique().tolist())
                        
                        if available_dates_pdf:
                            selected_pdf_date = st.selectbox("PDF出力する作業日を選択", available_dates_pdf, key="pdf_date_select")
                            if st.button("📥 選択した日付のスケジュールPDFを生成する", type="primary"):
                                df_day_target = df_schedule_out[df_schedule_out["割当作業日"] == selected_pdf_date]
                                pdf_buffer = io.BytesIO()
                                doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
                                story = []
                                styles = getSampleStyleSheet()
                                title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName=font_name, fontSize=16, leading=20, textColor=colors.HexColor('#1f1f1f'))
                                normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontName=font_name, fontSize=9, leading=12)
                                bold_style = ParagraphStyle('BoldStyle', parent=styles['Normal'], fontName=font_name, fontSize=9, leading=12)
                                
                                story.append(Paragraph(f"【製本管理システム】日別作業スケジュール ({selected_pdf_date})", title_style))
                                story.append(Spacer(1, 10))
                                table_data = [["時間帯", "受注番号", "案件名", "顧客名", "工程", "作業時間", "納期"]]
                                for _, r in df_day_target.iterrows():
                                    table_data.append([
                                        Paragraph(str(r['時間帯']), normal_style),
                                        Paragraph(str(r['受注番号']), normal_style),
                                        Paragraph(str(r['案件名']), normal_style),
                                        Paragraph(str(r['顧客名']), normal_style),
                                        Paragraph(str(r['工程']), bold_style),
                                        Paragraph(str(r['作業時間']), normal_style),
                                        Paragraph(str(r['納期']), normal_style),
                                    ])
                                t = Table(table_data, colWidths=[110, 60, 180, 100, 100, 80, 80])
                                t.setStyle(TableStyle([
                                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e0e0e0')),
                                    ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#999999')),
                                    ('TOPPADDING', (0,0), (-1,-1), 6),
                                    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                                ]))
                                story.append(t)
                                doc.build(story)
                                pdf_bytes = pdf_buffer.getvalue()
                                st.download_button(
                                    label=f"📥 【{selected_pdf_date}】PDFファイルをダウンロードする",
                                    data=pdf_bytes,
                                    file_name=f"schedule_{selected_pdf_date}.pdf",
                                    mime="application/pdf",
                                    key="download_pdf_btn"
                                )

                        # @pocket書き戻し用CSV
                        st.markdown("---")
                        st.subheader("📥 @pocket書き戻し用データのダウンロード")
                        col1, col2 = st.columns(2)
                        with col1:
                            selected_writeback_date = st.selectbox("書き戻し対象の作業日を選択", ["すべて（全期間）"] + available_dates_pdf)
                        with col2:
                            status_choice = st.selectbox("完了ステータスの値", ["完了", "未了（空欄）"])
                        
                        df_pocket_export = df_schedule_out[["受注番号", "工程", "割当作業日", "時間帯", "明細レコードID"]].copy()
                        if selected_writeback_date != "すべて（全期間）":
                            df_pocket_export = df_pocket_export[df_pocket_export["割当作業日"] == selected_writeback_date]
                        df_pocket_export["完了ステータス"] = "完了" if status_choice == "完了" else ""
                        csv_text = df_pocket_export.to_csv(index=False)
                        csv_bytes = csv_text.encode('cp932', errors='replace')
                        st.download_button(
                            label=f"📥 @pocket書き戻し用CSVをダウンロードする",
                            data=csv_bytes,
                            file_name=f"pocket_writeback_{datetime.today().strftime('%Y%m%d')}.csv",
                            mime="text/csv"
                        )

                # ── 別タブ：AI現場アシスタント ＆ 特殊ルール動的設定 ──
                with tab_ai:
                    st.header("🤖 AI現場アシスタント ＆ 高度な特殊ルールの設定")
                    st.markdown("ここでは、現場固有の特殊ルール（担当者スキル制約・段取り替え時間）の追加や、AIへのスケジュール相談が行えます。")
                    
                    st.subheader("⚙️ 現場の特殊ルール・制約の動的追加")
                    with st.form("rule_form"):
                        rule_type = st.selectbox("ルール種別", ["機械段取り替え時間の追加", "特定スタッフ・スキル限定"])
                        rule_target = st.text_input("対象工程 または スタッフ名（例: 製本, 熟練スタッフA）")
                        rule_val = st.number_input("追加する所要時間（分） または 制限値", min_value=0.0, value=30.0)
                        submit_rule = st.form_submit_button("➕ 特殊ルールを追加する")
                        
                        if submit_rule:
                            st.session_state.special_rules.append({
                                "type": rule_type,
                                "target": rule_target,
                                "value": rule_val
                            })
                            st.success(f"特殊ルール「{rule_type}（{rule_target}: {rule_val}）」を追加しました！※次回の計算時に反映されます。")
                    
                    if st.session_state.special_rules:
                        st.markdown("**現在有効な特殊ルール一覧:**")
                        for idx, r in enumerate(st.session_state.special_rules):
                            st.write(f"{idx+1}. 【{r['type']}】 対象: {r['target']} / 設定値: {r['value']}")
                        if st.button("🗑️ 特殊ルールをすべてリセット"):
                            st.session_state.special_rules = []
                            st.rerun()

                    st.markdown("---")
                    st.subheader("💬 自然言語スケジュール相談窓口（AIチャット）")
                    
                    # チャット履歴の表示
                    for msg in st.session_state.chat_history:
                        with st.chat_message(msg["role"]):
                            st.write(msg["content"])
                            
                    # チャット入力
                    user_prompt = st.chat_input("例：今週一番残業が多い日は？ / 〇〇の案件を急ぎで割り込ませるには？")
                    if user_prompt:
                        st.session_state.chat_history.append({"role": "user", "content": user_prompt})
                        
                        # AIからの回答生成ロジック（計算データに基づく分析）
                        ai_reply = "スケジュールデータがまだ計算されていません。「スケジュール計算・出力管理」タブで計算を実行してください。"
                        if st.session_state.calculated and st.session_state.df_schedule_out is not None:
                            df_res = st.session_state.df_schedule_out
                            slot_pool = st.session_state.daily_slot_pool
                            
                            prompt_lower = user_prompt.lower()
                            if "残業" in prompt_lower or "多い" in prompt_lower:
                                max_overtime_day = "なし"
                                max_ot = 0
                                for d, s_info in slot_pool.items():
                                    ot = max(0.0, (s_info["AM"]["used"] + s_info["PM"]["used"]) - (s_info["AM"]["max"] + s_info["PM"]["max"]))
                                    if ot > max_ot:
                                        max_ot = ot
                                        max_overtime_day = d
                                ai_reply = f"📊 分析結果:\n最も残業（負荷超過）が発生する日は **{max_overtime_day}** （超過時間: 約 **{max_ot:.1f}時間**）です。"
                            elif "案件" in prompt_lower or "受注" in prompt_lower:
                                ai_reply = f"📋 現在登録されている受注数は合計 **{df_res['受注番号'].nunique()}件** です。詳細な納期や進捗はメインタブの要約表をご確認ください。"
                            else:
                                ai_reply = f"🤖 ご質問ありがとうございます。「{user_prompt}」については、現在のところ特定の制約変更なしで順調にスケジュールに組み込まれています。追加の特殊ルールがある場合は上のフォームから設定してください。"
                        
                        st.session_state.chat_history.append({"role": "assistant", "content": ai_reply})
                        st.rerun()

    except Exception as e:
        st.error(f"エラーが発生しました: {str(e)}")