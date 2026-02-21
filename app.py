import streamlit as st
import sqlite3
import pandas as pd
import imaplib
import email
import re
import requests
import asyncio
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession

# --- CONSTANTES ---
MI_API_ID = 34062718  
MI_API_HASH = 'ca9d5cbc6ce832c6660f949a5567a159'

# --- 1. CONFIGURACIÓN DE BASE DE DATOS (V6) ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix_v6.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  usuario TEXT UNIQUE, clave TEXT, estado INTEGER, fecha_vencimiento DATE)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS correos_madre (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,
                 correo_imap TEXT, password_app TEXT, servidor_imap TEXT DEFAULT 'imap.gmail.com',
                 filtro_login INTEGER DEFAULT 1, filtro_temporal INTEGER DEFAULT 1,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS bots_telegram (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,
                 bot_username TEXT, plataforma TEXT, string_session TEXT, recipe_steps TEXT,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_cliente TEXT UNIQUE, 
                  pass_cliente TEXT, vendedor_id INTEGER, estado_pago INTEGER DEFAULT 1,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    conn.close()

inicializar_db()

# --- LÓGICA DE EXTRACCIÓN: BOT DE TELEGRAM ---
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente):
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            await client.send_message(bot_username, email_cliente)
            await asyncio.sleep(4) 
            ultimos_msgs = await client.get_messages(bot_username, limit=1)
            return ultimos_msgs[0].text if ultimos_msgs else "Sin respuesta del bot."
    except Exception as e:
        return f"Error con Bot: {str(e)}"

# --- LÓGICA DE EXTRACCIÓN: CORREOS (IMAP) CON PREVISUALIZACIÓN ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        
        # Filtramos directamente por el correo del cliente final
        criterio = f'(TO "{email_cliente_final}")'
        status, mensajes = mail.search(None, criterio)
        
        if not mensajes[0]: return None 
        
        ultimo_id = mensajes[0].split()[-1]
        res, datos = mail.fetch(ultimo_id, '(RFC822)')
        msg = email.message_from_bytes(datos[0][1])
        
        cuerpo = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    cuerpo = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    break
        else:
            cuerpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

        # Filtros de seguridad del Vendedor
        es_login = "inicio de sesión" in cuerpo.lower() or "nuevo dispositivo" in cuerpo.lower() or "sign in" in cuerpo.lower()
        es_temporal = "temporal" in cuerpo.lower() or "viaje" in cuerpo.lower() or "travel" in cuerpo.lower()

        if es_login and not filtro_login:
            return "BLOQUEADO: El vendedor desactivó la entrega automática para Inicios de Sesión."
        if es_temporal and not filtro_temporal:
            return "BLOQUEADO: El vendedor desactivó la entrega automática para Accesos Temporales."

        return cuerpo # Devolvemos el HTML para previsualizar

    except Exception as e:
        return f"Error de conexión: {str(e)}"

# --- INTERFAZ DE USUARIO ---
st.set_page_config(page_title="Gestión de Cuentas v6.0", layout="centered")
menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Navegación", menu)

if 'admin_logueado' not in st.session_state: st.session_state['admin_logueado'] = False
if 'vendedor_logueado' not in st.session_state: st.session_state['vendedor_logueado'] = False

# ==========================================
# PANEL ADMINISTRADOR
# ==========================================
if opcion == "Administrador":
    st.header("🔑 Panel de Control Maestro")
    if not st.session_state['admin_logueado']:
        with st.form("form_login_admin"):
            c_maestra = st.text_input("Clave Maestra", type="password")
            if st.form_submit_button("Ingresar"):
                if c_maestra == "merida2026":
                    st.session_state['admin_logueado'] = True
                    st.rerun()
                else: st.error("Clave incorrecta.")
    else:
        if st.button("🚪 Cerrar Sesión Admin"):
            st.session_state['admin_logueado'] = False
            st.rerun()
        col_crear, col_lista = st.columns([1, 2])
        with col_crear:
            st.subheader("➕ Vendedor")
            nv = st.text_input("Usuario")
            cv = st.text_input("Contraseña")
            if st.button("Guardar"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                venc = (datetime.now() + timedelta(days=30)).date()
                conn.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", (nv, cv, 1, venc))
                conn.commit()
                conn.close()
                st.success("Guardado.")
        with col_lista:
            st.subheader("👥 Lista")
            conn = sqlite3.connect('gestion_netflix_v6.db')
            vendedores = conn.execute("SELECT * FROM vendedores").fetchall()
            for v in vendedores:
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                c1.write(v[1])
                c2.write("🟢" if v[3] else "🔴")
                if c3.button("🔄", key=f"stat_{v[0]}"):
                    conn.execute("UPDATE vendedores SET estado=? WHERE id=?", (0 if v[3] else 1, v[0])); conn.commit(); st.rerun()
                if c4.button("🗑️", key=f"del_{v[0]}"):
                    conn.execute("DELETE FROM vendedores WHERE id=?", (v[0],)); conn.commit(); st.rerun()
            conn.close()

# ==========================================
# PANEL VENDEDOR
# ==========================================
elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Portal Vendedores")
    if not st.session_state['vendedor_logueado']:
        with st.form("f_v"):
            u_v, p_v = st.text_input("Usuario"), st.text_input("Clave", type="password")
            if st.form_submit_button("Entrar"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                vend = conn.execute("SELECT id, estado FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()
                if vend and vend[1]:
                    st.session_state['vendedor_logueado'], st.session_state['id_vend_actual'] = True, vend[0]
                    st.rerun()
                else: st.error("Error de acceso.")
    else:
        if st.button("🚪 Salir"): st.session_state['vendedor_logueado'] = False; st.rerun()
        v_id = st.session_state['id_vend_actual']
        tab1, tab2 = st.tabs(["⚙️ Fuentes", "👥 Clientes"])
        conn = sqlite3.connect('gestion_netflix_v6.db')
        c = conn.cursor()
        with tab1:
            with st.form("f_m"):
                me, mp = st.text_input("Correo"), st.text_input("Pass App", type="password")
                fl, ft = st.checkbox("Login", True), st.checkbox("Temporal", True)
                if st.form_submit_button("Añadir Correo"):
                    c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, filtro_login, filtro_temporal) VALUES (?,?,?,?,?)", (v_id, me, mp, int(fl), int(ft))); conn.commit(); st.rerun()
            for cg in c.execute("SELECT id, correo_imap FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall():
                col1, col2 = st.columns([5, 1])
                col1.caption(cg[1])
                if col2.button("🗑️", key=f"d_c_{cg[0]}"): c.execute("DELETE FROM correos_madre WHERE id=?", (cg[0],)); conn.commit(); st.rerun()
        with tab2:
            with st.form("f_c"):
                cu, cp = st.text_input("User Web"), st.text_input("Pass Web")
                if st.form_submit_button("Crear Cliente"):
                    c.execute("INSERT INTO cuentas (usuario_cliente, pass_cliente, vendedor_id) VALUES (?,?,?)", (cu, cp, v_id)); conn.commit(); st.rerun()
            for cli in c.execute("SELECT id, usuario_cliente, estado_pago, pass_cliente FROM cuentas WHERE vendedor_id=?", (v_id,)).fetchall():
                cc1, cc2, cc3 = st.columns([3, 1, 1])
                cc1.write(f"👤 {cli[1]} | 🔑 `{cli[3]}`")
                if cc2.button("🟢" if cli[2] else "🔴", key=f"p_{cli[0]}"):
                    c.execute("UPDATE cuentas SET estado_pago=? WHERE id=?", (0 if cli[2] else 1, cli[0])); conn.commit(); st.rerun()
                if cc3.button("🗑️", key=f"d_cl_{cli[0]}"): c.execute("DELETE FROM cuentas WHERE id=?", (cli[0],)); conn.commit(); st.rerun()
        conn.close()

# ==========================================
# PANEL CLIENTE
# ==========================================
elif opcion == "Panel Cliente":
    st.header("📺 Buscador")
    if 'c_log' not in st.session_state: st.session_state['c_log'] = False
    if not st.session_state['c_log']:
        with st.form("l_c"):
            u, p = st.text_input("Usuario"), st.text_input("Clave", type="password")
            if st.form_submit_button("Entrar"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                res = conn.execute("SELECT vendedor_id, estado_pago FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?", (u, p)).fetchone()
                if res and res[1]:
                    st.session_state['c_log'], st.session_state['v_id'] = True, res[0]; st.rerun()
                else: st.error("Error.")
    else:
        if st.button("Cerrar"): st.session_state['c_log'] = False; st.rerun()
        plat = st.selectbox("Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
        correo_buscar = st.text_input("Correo de la cuenta:")
        if st.button("Extraer Código"):
            conn = sqlite3.connect('gestion_netflix_v6.db')
            v_id = st.session_state['v_id']
            correos = conn.execute("SELECT correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()
            bots = conn.execute("SELECT bot_username, string_session, receta_steps, plataforma FROM bots_telegram WHERE vendedor_id=?", (v_id,)).fetchall()
            conn.close()
            found = None
            with st.spinner('Buscando...'):
                for m in correos:
                    if not found: found = obtener_codigo_centralizado(m[0], m[1], correo_buscar, plat, m[2], m[3], m[4])
                if not found:
                    for b in bots:
                        if not found and (b[3] == "Todas las plataformas" or b[3] == plat):
                            found = asyncio.run(ejecutar_receta_bot(b[1], b[0], b[2], correo_buscar))
            if found:
                if "BLOQUEADO" in str(found) or "Error" in str(found): st.error(found)
                else:
                    st.success("✅ Correo encontrado:")
                    # Renderiza el HTML del correo
                    st.components.v1.html(found, height=600, scrolling=True)
            else: st.error("No se encontró nada.")
