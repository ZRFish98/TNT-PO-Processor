import streamlit as st

def apply_custom_styles():
    st.markdown("""
    <style>
    /* OLED Background */
    .stApp {
        background-color: #000000;
        color: #FFFFFF;
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #0A0A0A !important;
        border-right: 1px solid #333333;
    }
    
    section[data-testid="stSidebar"] .st-emotion-cache-6qob1r {
        background-color: #0A0A0A;
    }

    /* Main Title and Headers */
    h1, h2, h3 {
        background: linear-gradient(90deg, #00d4ff 0%, #a100ff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: 800 !important;
        letter-spacing: -1px;
    }

    /* Cards/Containers */
    div[data-testid="stVerticalBlock"] > div > div[data-testid="stVerticalBlock"] {
        background-color: #111111;
        padding: 2rem;
        border-radius: 15px;
        border: 1px solid #222222;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        margin-bottom: 1rem;
    }

    /* Buttons */
    .stButton > button {
        background: linear-gradient(135deg, #00d4ff 0%, #0072ff 100%) !important;
        color: white !important;
        font-weight: 600 !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 0.5rem 1.5rem !important;
        transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) !important;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-size: 0.8rem;
    }

    .stButton > button:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 15px rgba(0, 212, 255, 0.4);
        border: none !important;
    }

    /* Input Fields */
    .stTextInput > div > div > input {
        background-color: #1A1A1A !important;
        color: #FFFFFF !important;
        border: 1px solid #333333 !important;
        border-radius: 8px !important;
    }
    
    .stTextInput > div > div > input:focus {
        border-color: #00d4ff !important;
        box-shadow: 0 0 10px rgba(0, 212, 255, 0.2) !important;
    }

    /* Metrics Styling */
    [data-testid="stMetricValue"] {
        font-size: 2.2rem !important;
        font-weight: 700 !important;
        color: #00d4ff !important;
    }

    /* Progress Bar */
    .stProgress > div > div > div > div {
        background-image: linear-gradient(to right, #00d4ff, #a100ff) !important;
    }

    /* Custom Scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
    }
    ::-webkit-scrollbar-track {
        background: #000000;
    }
    ::-webkit-scrollbar-thumb {
        background: #333333;
        border-radius: 4px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: #555555;
    }

    /* Glassmorphism Effect for certain elements */
    .glass-card {
        background: rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 15px;
        padding: 20px;
    }
    </style>
    """, unsafe_allow_html=True)
