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

    

    # Fuentes de extracción múltiples

    c.execute('''CREATE TABLE IF NOT EXISTS correos_madre (

                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,

                 correo_imap TEXT, password_app TEXT, servidor_imap TEXT DEFAULT 'imap.gmail.com',

                 filtro_login INTEGER DEFAULT 1, filtro_temporal INTEGER DEFAULT 1,

                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')



    c.execute('''CREATE TABLE IF NOT EXISTS bots_telegram (

                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,

                 bot_username TEXT, plataforma TEXT, string_session TEXT, recipe_steps TEXT,

                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')



    # CRM Simplificado: Controla el acceso web del cliente

    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 

                 (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_cliente TEXT UNIQUE, 

                  pass_cliente TEXT, vendedor_id INTEGER, estado_pago INTEGER DEFAULT 1,

                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')

    conn.commit()

    conn.close()



inicializar_db()



# --- LÓGICA DE EXTRACCIÓN: BOT DE TELEGRAM ---

async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):

    logs = []

    session_str = session_str.strip()

    try:

        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:

            await client.send_message(bot_username, email_cliente) # Enviamos directo el correo a buscar

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



        if es_login and not filtro_login:

            return "BLOQUEADO: El vendedor desactivó la entrega automática para Inicios de Sesión."

        if es_temporal and not filtro_temporal:

            return "BLOQUEADO: El vendedor desactivó la entrega automática para Accesos Temporales."



        if plataforma == "Prime Video":

            match = re.search(r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})', cuerpo, re.IGNORECASE)

            return match.group(1) if match else None

        else:

            links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo)

            link_n = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l]

            if not link_n: return None

            resp = requests.get(link_n[0])

            nums = [n for n in re.findall(r'\b\d{4}\b', resp.text) if n not in ["2024", "2025", "2026"]]

            return nums[0] if nums else None

    except Exception as e:

        return None 



# --- INTERFAZ DE USUARIO ---

st.set_page_config(page_title="Gestión de Cuentas v6.0", layout="centered")

menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]

opcion = st.sidebar.selectbox("Navegación", menu)



# --- INICIALIZACIÓN DE VARIABLES DE SESIÓN ---

if 'admin_logueado' not in st.session_state:

    st.session_state['admin_logueado'] = False

if 'vendedor_logueado' not in st.session_state:

    st.session_state['vendedor_logueado'] = False

if 'id_vend_actual' not in st.session_state:

    st.session_state['id_vend_actual'] = None

if 'nombre_vend_actual' not in st.session_state:

    st.session_state['nombre_vend_actual'] = ""



# ==========================================

# PANEL ADMINISTRADOR

# ==========================================

if opcion == "Administrador":

    st.header("🔑 Panel de Control Maestro")

    

    # Pantalla de Login (Evita recargas en móvil)

    if not st.session_state['admin_logueado']:

        with st.form("form_login_admin"):

            c_maestra = st.text_input("Clave Maestra", type="password")

            btn_ingresar_admin = st.form_submit_button("Ingresar")

            

            if btn_ingresar_admin:

                if c_maestra == "merida2026":

                    st.session_state['admin_logueado'] = True

                    st.rerun()

                else:

                    st.error("Clave incorrecta.")

    

    # Panel Interno (Solo se ve si está logueado)

    else:

        if st.button("🚪 Cerrar Sesión Admin"):

            st.session_state['admin_logueado'] = False

            st.rerun()

            

        st.markdown("---")

        col_crear, col_lista = st.columns([1, 2])

        

        with col_crear:

            st.subheader("➕ Registrar Vendedor")

            nv = st.text_input("Usuario")

            cv = st.text_input("Contraseña")

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

                else:

                    st.warning("Llena los campos.")



        with col_lista:

            st.subheader("👥 Vendedores")

            conn = sqlite3.connect('gestion_netflix_v6.db')

            vendedores = conn.execute("SELECT * FROM vendedores").fetchall()

            for v in vendedores:

                c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1])

                c1.write(f"**{v[1]}** (Pass: `{v[2]}`)")

                c2.write("🟢 Activo" if v[3] else "🔴 Inactivo")

                if c3.button("Estado", key=f"v_stat_{v[0]}"):

                    conn.execute("UPDATE vendedores SET estado=? WHERE id=?", (0 if v[3] else 1, v[0]))

                    conn.commit()

                    st.rerun()

                if c4.button("🗑️", key=f"v_del_{v[0]}"):

                    conn.execute("DELETE FROM correos_madre WHERE vendedor_id=?", (v[0],))

                    conn.execute("DELETE FROM bots_telegram WHERE vendedor_id=?", (v[0],))

                    conn.execute("DELETE FROM cuentas WHERE vendedor_id=?", (v[0],))

                    conn.execute("DELETE FROM vendedores WHERE id=?", (v[0],))

                    conn.commit()

                    st.rerun()

            conn.close()



# ==========================================

# PANEL VENDEDOR

# ==========================================

elif opcion == "Panel Vendedor":

    st.header("👨‍💼 Portal de Vendedores")

    

    # Pantalla de Login (Evita recargas en móvil)

    if not st.session_state['vendedor_logueado']:

        with st.form("form_login_vendedor"):

            u_v = st.text_input("Usuario")

            p_v = st.text_input("Clave", type="password")

            btn_ingresar_vend = st.form_submit_button("Iniciar Sesión")

            

            if btn_ingresar_vend:

                if u_v and p_v:

                    conn = sqlite3.connect('gestion_netflix_v6.db')

                    vend = conn.execute("SELECT id, estado, usuario FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()

                    conn.close()

                    

                    if vend:

                        if vend[1] == 1:

                            st.session_state['vendedor_logueado'] = True

                            st.session_state['id_vend_actual'] = vend[0]

                            st.session_state['nombre_vend_actual'] = vend[2]

                            st.rerun()

                        else:

                            st.error("Tu cuenta está desactivada. Contacta al administrador.")

                    else:

                        st.error("Credenciales incorrectas.")

                else:

                    st.warning("Llena los campos.")

    

    # Panel Interno (Solo se ve si está logueado)

    else:

        st.success(f"Bienvenido, {st.session_state['nombre_vend_actual']}")

        if st.button("🚪 Cerrar Sesión"):

            st.session_state['vendedor_logueado'] = False

            st.session_state['id_vend_actual'] = None

            st.rerun()

            

        st.markdown("---")

        v_id = st.session_state['id_vend_actual']

        conn = sqlite3.connect('gestion_netflix_v6.db')

        c = conn.cursor()

            

        tab_fuentes, tab_clientes = st.tabs(["⚙️ Fuentes de Extracción", "👥 Gestión de Clientes"])

        

        with tab_fuentes:

            st.info("Puedes registrar todos los correos y bots que necesites. El sistema buscará en todos ellos automáticamente.")

            

            # --- SECCIÓN: CORREOS ---

            st.subheader("📧 Mis Correos (Gmail / Dominios Privados)")

            with st.form("f_madre"):

                tipo_correo = st.radio("Tipo de proveedor:", ["Gmail / Google Workspace", "Webmail (Dominio Privado / cPanel)", "Outlook / Hotmail"])

                me = st.text_input("Correo Electrónico")

                mp = st.text_input("Contraseña (o Clave de Aplicación)", type="password")

                

                servidor_personalizado = "imap.gmail.com"

                if tipo_correo == "Webmail (Dominio Privado / cPanel)":

                    servidor_personalizado = st.text_input("Servidor IMAP", value="mail.tudominio.com")

                elif tipo_correo == "Outlook / Hotmail":

                    servidor_personalizado = "outlook.office365.com"



                st.write("**Filtros de Seguridad:**")

                col_f1, col_f2 = st.columns(2)

                f_log = col_f1.checkbox("Permitir Nuevo Inicio de Sesión", value=True)

                f_tmp = col_f2.checkbox("Permitir Acceso Temporal", value=True)

                

                if st.form_submit_button("Añadir Correo"):

                    c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal) VALUES (?,?,?,?,?,?)", 

                              (v_id, me, mp, servidor_personalizado, int(f_log), int(f_tmp)))

                    conn.commit()

                    st.success("Correo añadido.")

                    st.rerun()

            

            # Mostrar correos registrados con botón de eliminar

            correos_guardados = c.execute("SELECT id, correo_imap, servidor_imap FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()

            if correos_guardados:

                st.write("**Tus correos activos:**")

                for cg in correos_guardados:

                    cc1, cc2 = st.columns([5, 1])

                    cc1.caption(f"✅ {cg[1]} ({cg[2]})")

                    if cc2.button("🗑️", key=f"del_cm_{cg[0]}"):

                        c.execute("DELETE FROM correos_madre WHERE id=?", (cg[0],))

                        conn.commit()

                        st.rerun()



            st.markdown("---")

            

            # --- SECCIÓN: BOTS ---

            st.subheader("🤖 Mis Bots de Telegram")

            with st.form("f_bot"):

                b_user = st.text_input("Username del Bot (@ejemplo_bot)")

                plat_bot = st.selectbox("¿Para qué plataforma es este bot?", ["Todas las plataformas", "Netflix", "Prime Video", "Disney+", "Otros"])

                s_sess = st.text_area("String Session (Llave)")

                r_steps = st.text_area("Receta de Pasos (Opcional)")

                if st.form_submit_button("Añadir Bot"):

                    c.execute("INSERT INTO bots_telegram (vendedor_id, bot_username, plataforma, string_session, recipe_steps) VALUES (?,?,?,?,?)", 

                              (v_id, b_user, plat_bot, s_sess, r_steps))

                    conn.commit()

                    st.success("Bot añadido.")

                    st.rerun()

            

            # Mostrar bots registrados con botón de eliminar

            bots_guardados = c.execute("SELECT id, bot_username, plataforma FROM bots_telegram WHERE vendedor_id=?", (v_id,)).fetchall()

            if bots_guardados:

                st.write("**Tus bots activos:**")

                for bg in bots_guardados:

                    bc1, bc2 = st.columns([5, 1])

                    bc1.caption(f"✅ {bg[1]} ({bg[2]})")

                    if bc2.button("🗑️", key=f"del_bot_{bg[0]}"):

                        c.execute("DELETE FROM bots_telegram WHERE id=?", (bg[0],))

                        conn.commit()

                        st.rerun()



        with tab_clientes:

            st.subheader("➕ Crear Acceso para Cliente")

            st.write("Crea un usuario y clave para que tu cliente pueda entrar a la web a buscar sus códigos.")

            with st.form("f_cliente_nuevo"):

                c_user = st.text_input("Usuario web (Ej: carlos_perez)")

                c_pass = st.text_input("Clave web")

                

                if st.form_submit_button("Registrar Cliente"):

                    try:

                        c.execute("INSERT INTO cuentas (usuario_cliente, pass_cliente, vendedor_id) VALUES (?,?,?)", (c_user, c_pass, v_id))

                        conn.commit()

                        st.success("Acceso creado. Entrégale estos datos a tu cliente.")

                        st.rerun()

                    except: st.error("Ese usuario web ya existe. Intenta con otro.")

            

            st.markdown("---")

            st.subheader("📋 Control de Pagos de Clientes")

            clientes = c.execute("SELECT id, usuario_cliente, estado_pago, pass_cliente FROM cuentas WHERE vendedor_id=?", (v_id,)).fetchall()
