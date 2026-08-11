import streamlit as st

st.set_page_config(page_title="Prompt Lab", page_icon=":material/science:", layout="wide")

page = st.navigation([
    st.Page("app_pages/compare.py", title="Compare", icon=":material/compare_arrows:"),
    st.Page("app_pages/prompts.py", title="Prompts", icon=":material/edit_note:"),
    st.Page("app_pages/handoff.py", title="Handoff", icon=":material/engineering:"),
])

page.run()
