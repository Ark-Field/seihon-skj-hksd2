import streamlit as st
import pandas as pd
import io

# ページ基本設定
st.set_page_config(page_title="動作試験用アプリ", layout="centered")

st.title("🧪 製本管理システム - 動作試験用画面")

st.write("CSVファイルをアップロードして、データが正しく読み込めるかテストします。")

# アップロードフォーム
uploaded_file = st.file_uploader("CSVファイルを選択してください", type=["csv"])

if uploaded_file is not None:
    try:
        # CSVの読み込み
        df = pd.read_csv(io.StringIO(uploaded_file.read().decode('utf-8')))
        
        st.success("CSVファイルの読み込みに成功しました！")
        
        # データのプレビュー表示
        st.subheader("📋 読み込んだデータ（プレビュー）")
        st.dataframe(df, use_container_width=True)
        
        # 行数・列数の表示
        st.info(f"行数: {df.shape[0]}行 / 列数: {df.shape[1]}列")
        
    except Exception as e:
        st.error(f"ファイルの読み込み中にエラーが発生しました: {str(e)}")
else:
    st.info("上のボタンからCSVファイルをアップロードしてください。")