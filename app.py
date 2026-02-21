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
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            await client.send_message(bot_username, email_cliente) 
            await asyncio.sleep(4) 
            
            ultimos_msgs = await client.get_messages(bot_username, limit=1)
            respuesta = ultimos_msgs[0].text if ultimos_msgs else "Sin respuesta del bot."
            return respuesta
    except Exception as e:
        return f"Error con Bot: {str(e)}"

# --- LÓGICA DE EXTRACCIÓN: CORREOS (IMAP) CON FILTROS ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")' if plataforma == "Prime Video" else f'(FROM "info@account.netflix.com" TO "{email_cliente_final}")'
        status, mensajes = mail.search(None, criterio)
        if not mensajes[0]: return None 
        
        ultimo_id = mensajes[0].split()[-1]
        res, datos = mail.fetch(ultimo_id, '(RFC822)')
        msg = email.message_from_bytes(datos[0][1])
        cuerpo = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ["text/plain", "text/html"]:
                    cuerpo += part.get_payload(decode=True).decode('utf-8', errors='ignore')
        else:
            cuerpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

        es_login = "inicio de sesión" in cuerpo.lower() or "nuevo dispositivo" in cuerpo.lower()
        es_temporal = "temporal" in cuerpo.lower() or "viaje" in cuerpo.lower() or "travel" in cuerpo.lower()
        tipo_msg = "Temporal" if es_temporal else "Login"

        if es_login and not filtro_login:
            return "BLOQUEADO: El vendedor desactivó la entrega automática para Inicios de Sesión."
        if es_temporal and not filtro_temporal:
            return "BLOQUEADO: El vendedor desactivó la entrega automática para Accesos Temporales."

        if plataforma == "Prime Video":
            match = re.search(r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})', cuerpo, re.IGNORECASE)
            return (match.group(1), tipo_msg) if match else None
        else:
            links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo)
            link_n = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l]
            if not link_n: return None
            resp = requests.get(link_n[0])
            nums = [n for n in re.findall(r'\b\d{4}\b', resp.text) if n not in ["2024", "2025", "2026"]]
            return (nums[0], tipo_msg) if nums else None
    except Exception as e:
        return None 

# --- FUNCIÓN NUEVA: PLANTILLAS VISUALES ---
def renderizar_plantilla_correo(plataforma, codigo, tipo="Login"):
    estilos = {
        "Netflix": {"bg": "#000000", "acc": "#E50914", "logo": "https://assets.nflxext.com/us/email/logo/netflix-logo-v2.png"},
        "Disney+": {"bg": "#1A1D29", "acc": "#0072D2", "logo": "https://static-assets.bamgrid.com/product/disneyplus/images/logo.1.5.png"},
        "Prime Video": {"bg": "#ffffff", "acc": "#00A8E1", "logo": "https://upload.wikimedia.org/wikipedia/commons/1/11/Amazon_Prime_Video_logo.svg"},
        "Otros": {"bg": "#f3f3f3", "acc": "#333333", "logo": ""}
    }
    config = estilos.get(plataforma, estilos["Otros"])
    color_texto = "#ffffff" if config["bg"] != "#ffffff" else "#000000"
    titulo = "Código de Acceso Temporal" if tipo == "Temporal" else "Código de Verificación"

    return f"""
    <div style="background-color: {config['bg']}; padding: 30px; border-radius: 15px; text-align: center; border: 1px solid #444; color: {color_texto}; font-family: Arial;">
        <img src="{config['logo']}" width="120" style="margin-bottom: 15px;">
        <h2 style="margin: 0; font-size: 1.1rem; opacity: 0.9;">{titulo}</h2>
        <p style="font-size: 0.8rem; opacity: 0.7;">Tu código para {plataforma} es:</p>
        <div style="background-color: {config['acc']}; color: white; display: inline-block; padding: 10px 30px; border-radius: 8px; margin: 15px 0; font-size: 2.2rem; font-weight: bold; letter-spacing: 4px;">
            {codigo}
        </div>
        <p style="font-size: 0.7rem; opacity: 0.5;">Solicitado el {datetime.now().strftime('%H:%M')}</p>
    </div>
    """

# --- INTERFAZ DE USUARIO ---
st.set_page_config(page_title="Gestión de Cuentas v6.0", layout="centered")
menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Navegación", menu)

if 'admin_logueado' not in st.session_state: st.session_state['admin_logueado'] = False
if 'vendedor_logueado' not in st.session_state: st.session_state['vendedor_logueado'] = False
if 'id_vend_actual' not in st.session_state: st.session_state['id_vend_actual'] = None
if 'nombre_vend_actual' not in st.session_state: st.session_state['nombre_vend_actual'] = ""

# --- PANEL ADMINISTRADOR (Toda tu lógica original se mantiene aquí) ---
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
        st.markdown("---")
        col_crear, col_lista = st.columns([1, 2])
        with col_crear:
            st.subheader("➕ Registrar Vendedor")
            nv, cv = st.text_input("Usuario"), st.text_input("Contraseña")
            if st.button("Guardar Vendedor"):
                if nv and cv:
                    conn = sqlite3.connect('gestion_netflix_v6.db')
                    try:
                        venc = (datetime.now() + timedelta(days=30)).date()
                        conn.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", (nv, cv, 1, venc))
                        conn.commit()
                        st.success("Vendedor guardado.")
                    except: st.error("Usuario ya existe.")
                    conn.close()
        with col_lista:
            st.subheader("👥 Vendedores")
            conn = sqlite3.connect('gestion_netflix_v6.db')
            vendedores = conn.execute("SELECT * FROM vendedores").fetchall()
            for v in vendedores:
                c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1])
                c1.write(f"**{v[1]}**")
                c2.write("🟢 Activo" if v[3] else "🔴 Inactivo")
                if c3.button("Estado", key=f"v_stat_{v[0]}"):
                    conn.execute("UPDATE vendedores SET estado=? WHERE id=?", (0 if v[3] else 1, v[0]))
                    conn.commit()
                    st.rerun()
                if c4.button("🗑️", key=f"v_del_{v[0]}"):
                    conn.execute("DELETE FROM vendedores WHERE id=?", (v[0],))
                    conn.commit()
                    st.rerun()
            conn.close()

# --- PANEL VENDEDOR (Toda tu lógica original se mantiene aquí) ---
elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Portal de Vendedores")
    if not st.session_state['vendedor_logueado']:
        with st.form("form_login_vendedor"):
            u_v, p_v = st.text_input("Usuario"), st.text_input("Clave", type="password")
            if st.form_submit_button("Iniciar Sesión"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                vend = conn.execute("SELECT id, estado, usuario FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()
                conn.close()
                if vend and vend[1] == 1:
                    st.session_state['vendedor_logueado'] = True
                    st.session_state['id_vend_actual'] = vend[0]
                    st.session_state['nombre_vend_actual'] = vend[2]
                    st.rerun()
                else: st.error("Credenciales incorrectas.")
    else:
        st.success(f"Bienvenido, {st.session_state['nombre_vend_actual']}")
        if st.button("🚪 Cerrar Sesión"):
            st.session_state['vendedor_logueado'] = False
            st.rerun()
        st.markdown("---")
        v_id = st.session_state['id_vend_actual']
        conn = sqlite3.connect('gestion_netflix_v6.db')
        c = conn.cursor()
        tab_fuentes, tab_clientes = st.tabs(["⚙️ Fuentes", "👥 Clientes"])
        with tab_fuentes:
            with st.form("f_madre"):
                tipo_correo = st.radio("Tipo:", ["Gmail / Google Workspace", "Webmail", "Outlook"])
                me, mp = st.text_input("Correo"), st.text_input("Clave App", type="password")
                f_log, f_tmp = st.checkbox("Login", value=True), st.checkbox("Temporal", value=True)
                if st.form_submit_button("Añadir"):
                    serv = "imap.gmail.com" if "Gmail" in tipo_correo else "outlook.office365.com"
                    c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal) VALUES (?,?,?,?,?,?)", 
                              (v_id, me, mp, serv, int(f_log), int(f_tmp)))
                    conn.commit()
                    st.rerun()
        with tab_clientes:
            with st.form("f_cli"):
                c_u, c_p = st.text_input("Usuario"), st.text_input("Clave")
                if st.form_submit_button("Crear Cliente"):
                    c.execute("INSERT INTO cuentas (usuario_cliente, pass_cliente, vendedor_id) VALUES (?,?,?)", (c_u, c_p, v_id))
                    conn.commit()
                    st.rerun()
        conn.close()

# --- PANEL CLIENTE (CON LAS MEJORAS VISUALES) ---
elif opcion == "Panel Cliente":
    st.header("📺 Buscador de Códigos")
    if 'cliente_logueado' not in st.session_state: st.session_state['cliente_logueado'] = False

    if not st.session_state['cliente_logueado']:
        with st.form("login_cliente"):
            u_l, p_l = st.text_input("Mi Usuario"), st.text_input("Mi Clave", type="password")
            if st.form_submit_button("Entrar"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                res = conn.execute("SELECT id, vendedor_id, estado_pago, usuario_cliente FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?", (u_l, p_l)).fetchone()
                conn.close()
                if res and res[2] == 1:
                    st.session_state['cliente_logueado'] = True
                    st.session_state['vendedor_id'] = res[1]
                    st.session_state['nombre_cli'] = res[3]
                    st.rerun()
                else: st.error("Usuario o clave incorrectos / Pago vencido.")
    else:
        st.success(f"Hola, {st.session_state['nombre_cli']}.")
        if st.button("Cerrar Sesión"):
            st.session_state['cliente_logueado'] = False
            st.rerun()

        st.markdown("---")
        plat = st.selectbox("Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
        correo_buscar = st.text_input("Correo de streaming:")
        
        if st.button("Extraer Código"):
            if correo_buscar:
                conn = sqlite3.connect('gestion_netflix_v6.db')
                v_id = st.session_state['vendedor_id']
                correos_vendedor = conn.execute("SELECT correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()
                bots_vendedor = conn.execute("SELECT bot_username, string_session, recipe_steps, plataforma FROM bots_telegram WHERE vendedor_id=?", (v_id,)).fetchall()
                conn.close()

                codigo_encontrado = None
                with st.spinner('Escaneando...'):
                    for madre in correos_vendedor:
                        if not codigo_encontrado:
                            codigo_encontrado = obtener_codigo_centralizado(madre[0], madre[1], correo_buscar, plat, madre[2], madre[3], madre[4])
                    
                    if not codigo_encontrado:
                        for bot in bots_vendedor:
                            if not codigo_encontrado and (bot[3] == "Todas las plataformas" or bot[3] == plat):
                                res_bot = asyncio.run(ejecutar_receta_bot(bot[1], bot[0], bot[2], correo_buscar))
                                if "Sin respuesta" not in res_bot and "Error" not in res_bot:
                                    codigo_encontrado = (res_bot, "Login") # Los bots suelen dar login por defecto

                if codigo_encontrado:
                    if isinstance(codigo_encontrado, tuple):
                        cod, tipo_msg = codigo_encontrado
                        st.balloons()
                        html_preview = renderizar_plantilla_correo(plat, cod, tipo_msg)
                        st.components.v1.html(html_preview, height=350)
                    elif "BLOQUEADO" in str(codigo_encontrado):
                        st.error(codigo_encontrado)
                else:
                    st.error("No se encontró ningún código reciente.")



