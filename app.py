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
        return cuerpo
    except Exception as e:
        return None

# --- INTERFAZ ---
st.set_page_config(page_title="Gestión Streaming v6.0", layout="centered")
opcion = st.sidebar.selectbox("Navegación", ["Panel Cliente", "Panel Vendedor", "Administrador"])

if 'admin_log' not in st.session_state: st.session_state['admin_log'] = False
if 'vend_log' not in st.session_state: st.session_state['vend_log'] = False

# ================= ADMINISTRADOR =================
if opcion == "Administrador":
    st.header("🔑 Administrador")
    if not st.session_state['admin_log']:
        with st.form("l_admin"):
            if st.text_input("Clave Maestra", type="password") == "merida2026" and st.form_submit_button("Ingresar"):
                st.session_state['admin_log'] = True; st.rerun()
    else:
        if st.button("Salir"): st.session_state['admin_log'] = False; st.rerun()
        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("➕ Registrar")
            u, p = st.text_input("Vendedor"), st.text_input("Clave")
            if st.button("Guardar"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                conn.execute("INSERT INTO vendedores (usuario, clave, estado) VALUES (?,?,1)", (u, p)); conn.commit(); conn.close(); st.success("Creado")
        with col2:
            st.subheader("👥 Lista de Vendedores")
            conn = sqlite3.connect('gestion_netflix_v6.db')
            for v in conn.execute("SELECT * FROM vendedores").fetchall():
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                c1.write(f"**{v[1]}** | `{v[2]}`") # <--- CLAVE VISIBLE PARA ADMIN
                c2.write("🟢" if v[3] else "🔴")
                if c3.button("🔄", key=f"s_{v[0]}"):
                    conn.execute("UPDATE vendedores SET estado=? WHERE id=?", (0 if v[3] else 1, v[0])); conn.commit(); st.rerun()
                if c4.button("🗑️", key=f"d_{v[0]}"):
                    conn.execute("DELETE FROM vendedores WHERE id=?", (v[0],)); conn.commit(); st.rerun()
            conn.close()

# ================= VENDEDOR =================
elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Vendedor")
    if not st.session_state['vend_log']:
        with st.form("l_v"):
            uv, pv = st.text_input("User"), st.text_input("Pass", type="password")
            if st.form_submit_button("Iniciar Sesión"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                res = conn.execute("SELECT id, estado FROM vendedores WHERE usuario=? AND clave=?", (uv, pv)).fetchone()
                if res and res[1]: 
                    st.session_state['vend_log'], st.session_state['vid'] = True, res[0]; st.rerun()
                else: st.error("Acceso denegado")
    else:
        if st.button("Cerrar Sesión"): st.session_state['vend_log'] = False; st.rerun()
        t1, t2 = st.tabs(["⚙️ Fuentes", "👥 Clientes"])
        v_id = st.session_state['vid']
        conn = sqlite3.connect('gestion_netflix_v6.db')
        c = conn.cursor()
        with t1:
            with st.form("f_m"):
                tipo = st.radio("Servidor", ["Gmail", "Outlook", "Privado"])
                me, mp = st.text_input("Correo"), st.text_input("Pass App (16 letras)", type="password")
                serv = "imap.gmail.com" if tipo == "Gmail" else "outlook.office365.com"
                if tipo == "Privado": serv = st.text_input("IMAP", "mail.tudominio.com")
                if st.form_submit_button("Añadir Correo"):
                    c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap) VALUES (?,?,?,?)", (v_id, me, mp, serv)); conn.commit(); st.rerun()
            
            with st.form("f_b"):
                bu = st.text_input("@Username_del_Bot")
                pl = st.selectbox("Plataforma", ["Todas", "Netflix", "Prime Video", "Disney+"])
                ss = st.text_area("String Session")
                if st.form_submit_button("Añadir Bot"):
                    c.execute("INSERT INTO bots_telegram (vendedor_id, bot_username, plataforma, string_session) VALUES (?,?,?,?)", (v_id, bu, pl, ss)); conn.commit(); st.rerun()
            
            st.write("---")
            for i in c.execute("SELECT id, correo_imap FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall():
                col1, col2 = st.columns([5,1]); col1.write(f"📧 {i[1]}")
                if col2.button("🗑️", key=f"dc_{i[0]}"): c.execute("DELETE FROM correos_madre WHERE id=?", (i[0],)); conn.commit(); st.rerun()
            for i in c.execute("SELECT id, bot_username FROM bots_telegram WHERE vendedor_id=?", (v_id,)).fetchall():
                col1, col2 = st.columns([5,1]); col1.write(f"🤖 {i[1]}")
                if col2.button("🗑️", key=f"db_{i[0]}"): c.execute("DELETE FROM bots_telegram WHERE id=?", (i[0],)); conn.commit(); st.rerun()

        with t2:
            with st.form("f_c"):
                cu, cp = st.text_input("Usuario Cliente"), st.text_input("Clave Cliente")
                if st.form_submit_button("Registrar Acceso"):
                    c.execute("INSERT INTO cuentas (usuario_cliente, pass_cliente, vendedor_id) VALUES (?,?,?)", (cu, cp, v_id)); conn.commit(); st.rerun()
            
            st.subheader("📋 Control de Pagos")
            for cli in c.execute("SELECT id, usuario_cliente, estado_pago, pass_cliente FROM cuentas WHERE vendedor_id=?", (v_id,)).fetchall():
                cc1, cc2, cc3 = st.columns([3, 1.5, 0.5])
                cc1.write(f"👤 **{cli[1]}** | 🔑 Clave: `{cli[3]}`") # <--- CLAVE VISIBLE PARA VENDEDOR
                if cc2.button("Activo" if cli[2] else "Vencido", key=f"p_{cli[0]}"):
                    c.execute("UPDATE cuentas SET estado_pago=? WHERE id=?", (0 if cli[2] else 1, cli[0])); conn.commit(); st.rerun()
                if cc3.button("🗑️", key=f"dcl_{cli[0]}"): c.execute("DELETE FROM cuentas WHERE id=?", (cli[0],)); conn.commit(); st.rerun()
        conn.close()

# ================= CLIENTE =================
else:
    st.header("📺 Buscador de Códigos")
    if 'cl_log' not in st.session_state: st.session_state['cl_log'] = False
    if not st.session_state['cl_log']:
        with st.form("login_cliente"):
            u, p = st.text_input("Mi Usuario"), st.text_input("Mi Clave", type="password")
            if st.form_submit_button("Ingresar"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                res = conn.execute("SELECT vendedor_id, estado_pago FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?", (u, p)).fetchone()
                if res and res[1]: 
                    st.session_state['cl_log'], st.session_state['v_id'] = True, res[0]; st.rerun()
                else: st.error("Acceso inactivo o incorrecto")
    else:
        if st.button("Cerrar Sesión"): st.session_state['cl_log'] = False; st.rerun()
        plat = st.selectbox("Selecciona Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
        correo = st.text_input("Correo de la cuenta de streaming")
        if st.button("Extraer Correo"):
            conn = sqlite3.connect('gestion_netflix_v6.db')
            v_id = st.session_state['v_id']
            correos = conn.execute("SELECT correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()
            bots = conn.execute("SELECT bot_username, string_session, plataforma FROM bots_telegram WHERE vendedor_id=?", (v_id,)).fetchall()
            conn.close(); found = None
            with st.spinner('Escaneando servidores...'):
                for m in correos:
                    if not found: found = obtener_codigo_centralizado(m[0], m[1], correo, plat, m[2], m[3], m[4])
                if not found:
                    for b in bots:
                        if not found and (b[2] == "Todas" or b[2] == plat):
                            found = asyncio.run(ejecutar_receta_bot(b[1], b[0], "", correo))
            if found:
                st.success("✅ Correo reciente encontrado:")
                st.components.v1.html(found, height=600, scrolling=True)
            else: st.error("No se encontró ningún correo reciente.")
