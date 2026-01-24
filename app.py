import streamlit as st
import sqlite3
import pandas as pd
import imaplib
import email
import re
import requests
from datetime import datetime, timedelta

# --- 1. CONFIGURACIÓN VISUAL (ESTILO MÓVIL) ---
st.set_page_config(page_title="Generador VIP", page_icon="⚡")

st.markdown("""
    <style>
    .stApp { background-color: #0e1117; color: white; }
    .card {
        background: rgba(255, 255, 255, 0.05);
        padding: 15px;
        border-radius: 10px;
        border-left: 4px solid #E50914;
        margin-bottom: 10px;
    }
    .val-dias { color: #00ff00; font-weight: bold; }
    .val-vencido { color: #ff4b4b; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 2. BASE DE DATOS ACTUALIZADA ---
def init_db():
    conn = sqlite3.connect('sistema_v4.db')
    c = conn.cursor()
    # Tabla Vendedores (con fecha_vencimiento)
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY, usuario TEXT UNIQUE, clave TEXT, estado INTEGER, vence DATE)''')
    # Tabla Cuentas (Modelo Correo + PIN)
    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY, correo_netflix TEXT, pin_acceso TEXT, pass_app TEXT, vend_id INTEGER)''')
    conn.commit()
    conn.close()

init_db()

# --- 3. LÓGICA DE CORREO ---
def get_netflix_code(correo, pass_app):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(correo, pass_app)
        mail.select("inbox")
        _, data = mail.search(None, '(FROM "info@account.netflix.com" SUBJECT "Tu codigo de acceso temporal")')
        if not data[0]: return "Pide el código en la TV primero"
        
        msg = email.message_from_bytes(mail.fetch(data[0].split()[-1], '(RFC822)')[1][0][1])
        html = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html": html = part.get_payload(decode=True).decode()
        
        links = re.findall(r'href=[\'"]?([^\'" >]+)', html)
        url = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l][0]
        res = requests.get(url).text
        codes = [n for n in re.findall(r'\b\d{4}\b', res) if n not in ["2024", "2025", "2026"]]
        return codes[0] if codes else "Código no hallado en la web"
    except Exception as e:
        return f"Error: {str(e)}"

# --- 4. INTERFAZ ---
st.title("🎬 Generador de Códigos")
tabs = st.tabs(["📲 Cliente", "👨‍💼 Vendedor", "⚙️ Admin"])

# --- PANEL CLIENTE (CORREO + PIN) ---
with tabs[0]:
    st.subheader("Acceso Directo")
    c_user = st.text_input("Correo de la cuenta", placeholder="ejemplo@gmail.com")
    p_user = st.text_input("PIN de Acceso", type="password", placeholder="Tu PIN (ej: 123)")
    
    if st.button("GENERAR CÓDIGO"):
        conn = sqlite3.connect('sistema_v4.db')
        c = conn.cursor()
        # Buscamos la cuenta y verificamos si el vendedor dueño está activo y vigente
        query = """
            SELECT cuentas.correo_netflix, cuentas.pass_app, vendedores.estado, vendedores.vce 
            FROM cuentas 
            JOIN vendedores ON cuentas.vend_id = vendedores.id 
            WHERE cuentas.correo_netflix=? AND cuentas.pin_acceso=?
        """
        # (Nota: cambié 'vence' por 'vce' en el alias para evitar conflictos)
        c.execute("SELECT cuentas.correo_netflix, cuentas.pass_app, vendedores.estado, vendedores.vence FROM cuentas JOIN vendedores ON cuentas.vend_id = vendedores.id WHERE correo_netflix=? AND pin_acceso=?", (c_user, p_user))
        res = c.fetchone()
        conn.close()
        
        if res:
            correo_n, p_app, v_est, v_vence = res
            v_vence_dt = datetime.strptime(v_vence, '%Y-%m-%d').date()
            if v_est == 1 and v_vence_dt >= datetime.now().date():
                with st.spinner("Obteniendo código..."):
                    codigo = get_netflix_code(correo_n, p_app)
                    st.success(f"Tu código es: {codigo}")
            else: st.error("Servicio inactivo o vencido.")
        else: st.error("Datos incorrectos.")

# --- PANEL VENDEDOR ---
with tabs[1]:
    u_v = st.text_input("Usuario Vendedor", key="v_user")
    p_v = st.text_input("Clave Vendedor", type="password", key="v_pass")
    
    if u_v and p_v:
        conn = sqlite3.connect('sistema_v4.db')
        v = conn.cursor().execute("SELECT id, estado, vence FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()
        if v and v[1] == 1:
            st.info(f"Vence el: {v[2]}")
            with st.form("nueva_cuenta"):
                st.write("Registrar Cuenta")
                c_netflix = st.text_input("Correo Netflix")
                pin = st.text_input("PIN para el cliente (ej: 123)")
                app_p = st.text_input("Clave App Gmail (16 letras)")
                if st.form_submit_button("Guardar"):
                    conn.cursor().execute("INSERT INTO cuentas (correo_netflix, pin_acceso, pass_app, vend_id) VALUES (?,?,?,?)", (c_netflix, pin, app_p, v[0]))
                    conn.commit()
                    st.success("Cuenta guardada exitosamente")
            
            st.write("### Mis Cuentas Activas")
            df = pd.read_sql_query(f"SELECT correo_netflix, pin_acceso FROM cuentas WHERE vend_id={v[0]}", conn)
            st.dataframe(df, use_container_width=True)
        else: st.error("Acceso denegado.")
        conn.close()

# --- PANEL ADMIN (CON CONTADOR DE DÍAS) ---
with tabs[2]:
    adm = st.text_input("Clave Maestra", type="password")
    if adm == "merida2026":
        st.subheader("Control de Vendedores")
        
        # Formulario rápido
        with st.expander("➕ Nuevo Vendedor"):
            nv = st.text_input("Nombre")
            cv = st.text_input("Clave")
            if st.button("Crear Vendedor"):
                conn = sqlite3.connect('sistema_v4.db')
                f_vence = (datetime.now() + timedelta(days=30)).date()
                conn.cursor().execute("INSERT INTO vendedores (usuario, clave, estado, vence) VALUES (?,?,?,?)", (nv, cv, 1, f_vence))
                conn.commit()
                st.rerun()

        st.markdown("---")
        conn = sqlite3.connect('sistema_v4.db')
        vends = pd.read_sql_query("SELECT * FROM vendedores", conn)
        
        for _, r in vends.iterrows():
            v_dt = datetime.strptime(r['vence'], '%Y-%m-%d').date()
            dias_restantes = (v_dt - datetime.now().date()).days
            
            # Estilo del contador
            clase_dias = "val-dias" if dias_restantes > 0 else "val-vencido"
            
            st.markdown(f"""
                <div class="card">
                    <b>👤 {r['usuario']}</b> | Clave: {r['clave']}<br>
                    📅 Vence: {r['vence']} | <span class="{clase_dias}">Días restantes: {dias_restantes}</span>
                </div>
            """, unsafe_allow_html=True)
            
            if st.button(f"Alternar Estado: {r['usuario']}"):
                nuevo = 0 if r['estado'] == 1 else 1
                conn.cursor().execute("UPDATE vendedores SET estado=? WHERE id=?", (nuevo, r['id']))
                conn.commit()
                st.rerun()
        conn.close()
