import streamlit as st
import pandas as pd
import io
from datetime import datetime, timedelta

# ページ基本設定
st.set_page_config(page_title="製本管理業務 - 工程管理予定表システム", layout="wide")

st.title("📦 製本管理業務 工程管理予定表システム")

# アップロードフォームの作成
with st.form("upload_form"):
    st.subheader("1. 各種CSVデータの選択")
    f1 = st.file_uploader("CSV① 受注データ（必須）", type=["csv"])
    f2 = st.file_uploader("CSV② 休暇シフトデータ（任意）", type=["csv"])
    f3 = st.file_uploader("CSV③ 機械稼働NGデータ（任意）", type=["csv"])
    
    st.subheader("2. 基本設定パラメータ")
    base_staff = st.number_input("基本人員数（人）", min_value=1, value=10)
    deadline_days = st.number_input("完成期日（納期の何日前までに完了させるか）", min_value=0, value=1)
    
    submitted = st.form_submit_button("スケジュールを計算・生成する")

if submitted:
    if f1 is None:
        st.error("CSV①（受注データ）は必須です。ファイルをアップロードしてください。")
    else:
        try:
            # CSVの読み込み
            df_order = pd.read_csv(io.StringIO(f1.read().decode('utf-8')))
            
            df_shift = pd.DataFrame()
            if f2 is not None:
                df_shift = pd.read_csv(io.StringIO(f2.read().decode('utf-8')))
                
            df_ng = pd.DataFrame()
            if f3 is not None:
                df_ng = pd.read_csv(io.StringIO(f3.read().decode('utf-8')))
            
            # ★【ご要望対応】「工程」が空欄・空白・NaNの行を自動除外する
            if '工程' in df_order.columns:
                df_order = df_order.dropna(subset=['工程'])
                df_order = df_order[df_order['工程'].astype(str).str.strip() != '']
                df_order = df_order[df_order['工程'].astype(str).str.lower() != 'nan']
                
            if df_order.empty:
                st.error("有効な「工程」データが存在する行が見つかりませんでした。")
            else:
                # 工程の順番（順）を守るために受注番号と順でソート
                df_order = df_order.sort_values(by=["受注番号", "順"])
                
                scheduled_results = []
                daily_summary = {}
                
                for _, row in df_order.iterrows():
                    customer = str(row.get("得意先", ""))
                    item_name = str(row.get("書名1", ""))
                    seq = row.get("順", 1)
                    process = str(row.get("工程", ""))
                    staff_needed = int(row.get("人数", 1))
                    duration_slots = float(row.get("所要時間（30分単位）", 2))
                    hours = duration_slots * 0.5  # 30分単位を時間に換算
                    due_date_str = str(row.get("納期日付", ""))
                    
                    # 納期から逆算した完成期日（納期の●日前）の算出
                    try:
                        due_date = datetime.strptime(due_date_str.strip(), "%Y-%m-%d")
                        target_date = due_date - timedelta(days=deadline_days)
                        target_date_str = target_date.strftime("%Y-%m-%d")
                    except:
                        target_date_str = due_date_str
                        
                    # 予定結果の格納（1タスク単位）
                    scheduled_results.append({
                        "日付": target_date_str,
                        "順番": seq,
                        "案件名": item_name,
                        "顧客名": customer,
                        "工程": process,
                        "人員": f"{staff_needed}人",
                        "時間": f"{hours}時間"
                    })
                    
                    # 日ごとの人員・時間集計
                    if target_date_str not in daily_summary:
                        daily_summary[target_date_str] = {"total_staff": staff_needed, "total_hours": hours}
                    else:
                        daily_summary[target_date_str]["total_staff"] += staff_needed
                        daily_summary[target_date_str]["total_hours"] += hours
                        
                st.success("スケジュールの計算が完了しました！")
                
                # ── 出力①：詳細スケジュール ──
                st.markdown("### 📋 詳細スケジュール")
                df_schedule_out = pd.DataFrame(scheduled_results)
                st.dataframe(df_schedule_out, use_container_width=True)
                
                # ── 出力②：日ごとの人員・時間合計 ＆ 過不足チェック ──
                st.markdown("### 📊 日ごとの人員・時間合計 ＆ 過不足チェック")
                summary_list = []
                for d, vals in daily_summary.items():
                    diff = base_staff - vals['total_staff']
                    status = f"余裕あり (+{diff}人)" if diff >= 0 else f"不足（{abs(diff)}人超過）"
                    summary_list.append({
                        "日付": d,
                        "合計人員": f"{vals['total_staff']}人",
                        "合計時間": f"{vals['total_hours']}時間",
                        "過不足ステータス": status
                    })
                df_summary_out = pd.DataFrame(summary_list)
                st.dataframe(df_summary_out, use_container_width=True)
                
        except Exception as e:
            st.error(f"エラーが発生しました: {str(e)}")