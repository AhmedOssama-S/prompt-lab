from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Prompt Lab", page_icon=":material/science:", layout="wide")

# st.navigation's page list always claims the very top of the sidebar,
# regardless of what else is written to st.sidebar or when -- st.logo() is the
# one element Streamlit places above it. assets/logo.svg is a plain text
# wordmark (no image asset exists for this internal tool), with the "o" in
# magenta matching the real logo's magenta "O" from the brand reference.
st.logo(image=str(Path(__file__).parent / "assets" / "logo.svg"), size="large")

page = st.navigation([
    st.Page("app_pages/compare.py", title="Compare", icon=":material/compare_arrows:"),
    st.Page("app_pages/prompts.py", title="Prompts", icon=":material/edit_note:"),
    st.Page("app_pages/handoff.py", title="Handoff", icon=":material/engineering:"),
])

page.run()
