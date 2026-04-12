# BDL Design System — Agent Prompt

Paste the prompt below into the agent in any Streamlit project folder.
Replace `<App Name>` and `<App subtitle goes here>` with the dashboard's name and one-line description.

---

## Prompt

```
You are redesigning a Streamlit app to match the Bodhi & Digby (BDL) design system.
The master reference implementation is at /mnt/homepage/app.py — read it first.

═══════════════════════════════════════════════════════
BRAND COLOURS
═══════════════════════════════════════════════════════
Deep navy:   #1a4a6b
Mid navy:    #1d5f82
Sky blue:    #29aae1
Bright cyan: #00b4d8
Lime green:  #8dc63f
White:       #ffffff

═══════════════════════════════════════════════════════
ASSETS  (copy from /mnt/homepage/Logos/ into this project's Logos/ folder)
═══════════════════════════════════════════════════════
Logo (transparent, no text): Logos/Logo Only - No text .png
Favicon:                      Logos/favicon.ico

═══════════════════════════════════════════════════════
PAGE CONFIG  (top of every app file)
═══════════════════════════════════════════════════════
st.set_page_config(
    page_title="<App Name> | Bodhi & Digby",
    page_icon="Logos/favicon.ico",
    layout="wide",
)

═══════════════════════════════════════════════════════
AUTOMATIC LIGHT / DARK MODE SWITCHING
═══════════════════════════════════════════════════════
Use JavaScript injected via st.markdown to read the browser's local hour and
write it to ?hour=N in the URL, triggering a single page reload:

    st.markdown("""
    <script>
    (function() {
        var params = new URLSearchParams(window.location.search);
        var hour = new Date().getHours();
        if (params.get('hour') !== String(hour)) {
            params.set('hour', hour);
            window.location.replace(
                window.location.pathname + '?' + params.toString());
        }
    })();
    </script>
    """, unsafe_allow_html=True)

Then in Python:
    try:
        hour = int(st.query_params.get("hour", -1))
    except (ValueError, TypeError):
        hour = -1
    if hour == -1:
        from datetime import datetime
        hour = datetime.now().hour
    dark_mode = hour >= 20 or hour < 7   # 20:00–06:59 = dark

Dark mode  → Design A (Navy Command Centre)
Light mode → Design B (Clean Light Professional)

═══════════════════════════════════════════════════════
DARK MODE CSS  (Design A — Navy Command Centre)
═══════════════════════════════════════════════════════
    #MainMenu, footer, header { visibility: hidden; }
    .stApp {
        background-color: #0b1a2e;
        color: #ffffff;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .block-container { padding-top: 0.5rem !important; }

    /* Header */
    .header-container {
        padding: 0.75rem 2rem 1.5rem 2rem;
        text-align: center;
        border-top: 6px solid #8dc63f;
    }
    .eyebrow {
        color: #29aae1;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        font-weight: 800;
        font-size: 0.9rem;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        color: #b0c4de;
        font-size: 1.2rem;
        max-width: 800px;
        margin: 0 auto 3rem auto;
    }

    /* Cards */
    .card {
        background: rgba(26, 74, 107, 0.4);
        border: 1px solid #29aae1;
        border-radius: 12px;
        padding: 2rem;
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        display: flex;
        flex-direction: column;
        transition: transform 0.3s ease;
    }
    .card:hover { transform: translateY(-5px); background: rgba(26,74,107,0.6); }
    .card-tag   { font-size:0.75rem; text-transform:uppercase; font-weight:700; color:#8dc63f; margin-bottom:0.5rem; }
    .card-title { font-size:1.8rem; font-weight:700; margin-bottom:1rem; color:#ffffff; }
    .card-desc  { font-size:1rem; line-height:1.6; color:#d1d5db; flex-grow:1; }

    /* Buttons */
    div[data-testid="stLinkButton"] a {
        background-color: #29aae1 !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 700 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        border-radius: 8px !important;
        text-decoration: none !important;
    }
    div[data-testid="stLinkButton"] a:hover { background-color: #1d8cb5 !important; }

    /* Footer */
    .footer {
        text-align: center;
        padding: 4rem 2rem 2rem 2rem;
        color: #64748b;
        font-size: 0.85rem;
        border-top: 1px solid rgba(255,255,255,0.05);
        margin-top: 4rem;
    }

═══════════════════════════════════════════════════════
LIGHT MODE CSS  (Design B — Clean Light Professional)
═══════════════════════════════════════════════════════
    #MainMenu, footer, header { visibility: hidden; }
    .stApp {
        background-color: #f4f7fa;
        color: #1a4a6b;
        font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }
    .block-container { padding-top: 0.5rem !important; }

    /* Header */
    .header-container {
        padding: 0.75rem 2rem 2rem 2rem;
        text-align: center;
    }
    .eyebrow { color: #1d5f82; font-weight:600; font-size:1rem; margin-bottom:0.5rem; }
    .sub-header { color:#64748b; font-size:1.25rem; max-width:750px; margin:0 auto; }

    /* Cards */
    .card {
        background: #ffffff;
        border-radius: 8px;
        padding: 2.5rem 2rem;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);
        display: flex;
        flex-direction: column;
        border-top-width: 5px;
        border-top-style: solid;
    }
    .card-tag   { font-size:0.85rem; font-weight:600; color:#00b4d8; margin-bottom:0.5rem; }
    .card-title { font-size:1.75rem; font-weight:700; margin-bottom:1rem; color:#1a4a6b; }
    .card-desc  { font-size:1rem; line-height:1.6; color:#64748b; flex-grow:1; }

    /* Buttons */
    div[data-testid="stLinkButton"] a {
        background-color: #1a4a6b !important;
        color: #ffffff !important;
        border: none !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
        text-decoration: none !important;
    }
    div[data-testid="stLinkButton"] a:hover { background-color: #29aae1 !important; }

    /* Footer */
    .footer {
        text-align: center;
        padding: 5rem 2rem 3rem 2rem;
        color: #94a3b8;
        font-size: 0.9rem;
    }

═══════════════════════════════════════════════════════
HEADER HTML  (inject after CSS, using an f-string)
═══════════════════════════════════════════════════════
Load the logo once at the top of the file:

    import base64
    with open("Logos/Logo Only - No text .png", "rb") as _f:
        _logo_b64 = base64.b64encode(_f.read()).decode()
    LOGO_URI = f"data:image/png;base64,{_logo_b64}"

Then render the header:

    st.markdown(f"""
    <div class="header-container">
        <div class="eyebrow">Bodhi &amp; Digby Business Suite</div>
        <div class="main-title">
            <img src="{LOGO_URI}"
                 alt="Bodhi &amp; Digby"
                 style="max-width:180px; width:100%; height:auto;
                        display:block; margin:0 auto;"/>
        </div>
        <div class="sub-header">
            <App subtitle goes here>
        </div>
    </div>
    """, unsafe_allow_html=True)

═══════════════════════════════════════════════════════
FOOTER HTML
═══════════════════════════════════════════════════════
    st.markdown("""
    <div class="footer">
        🔒 Secure internal tools &mdash; access managed via
        <a href="https://www.cloudflare.com/zero-trust/" target="_blank"
           style="color:inherit;">Cloudflare Zero Trust</a>.
        &nbsp;|&nbsp; Bodhi &amp; Digby Ltd &copy; 2026
    </div>
    """, unsafe_allow_html=True)

═══════════════════════════════════════════════════════
THEME BADGE  (optional but recommended)
═══════════════════════════════════════════════════════
Add to both CSS blocks:

    .theme-badge {
        position: fixed; bottom: 1rem; right: 1.2rem;
        font-size: 0.7rem; letter-spacing: 0.05em;
        color: #334155;   /* dark mode */
        color: #cbd5e1;   /* light mode */
    }

Then at the bottom of the app:

    mode_label = f"{'🌙 Dark' if dark_mode else '☀️ Light'} mode · {hour:02d}:xx"
    st.markdown(f'<div class="theme-badge">{mode_label}</div>',
                unsafe_allow_html=True)

═══════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════
1. Preserve ALL existing app logic, widgets, charts, and data — only the
   visual shell (page config, CSS, header, footer) should change.
2. Do NOT remove or alter any st.dataframe, st.chart, st.metric, st.form,
   or business-logic code.
3. Apply the CSS as a single st.markdown(css, unsafe_allow_html=True) block
   placed immediately after the theme detection logic.
4. Validate Python syntax with:
       python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
   Fix any errors before finishing.
5. After editing, confirm what was changed and what was intentionally left
   untouched.
```
