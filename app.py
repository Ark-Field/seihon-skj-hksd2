import streamlit as st

# ページのタイトル
st.title("🧪 動作テスト用アプリ")

# テキスト入力とボタンのテスト
name = st.text_input("あなたの名前を入力してください：")

if st.button("送信する"):
    if name:
        st.success(f"こんにちは、{name}さん！Streamlitは正常に動作しています 🎉")
    else:
        st.warning("名前を入力してください。")