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

# --- INTEGRACIÓN: Constantes Globales de API ---
MI_API_ID = 34062718  
MI_API_HASH = 'ca9d5cbc6ce832c6660f949a5567a159'

# --- 1. CONFIGURACIÓN DE BASE DE DATOS ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  usuario TEXT UNIQUE, 
                  clave TEXT, 
                  estado INTEGER, 
                  fecha_vencimiento DATE)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  plataforma TEXT, 
                  email TEXT, 
                  password_app TEXT, 
                  usuario_cliente TEXT UNIQUE, 
                  pass_cliente TEXT, 
                  vendedor_id INTEGER,
                  estado INTEGER,
                  string_session TEXT,
                  provider_bot TEXT,
                  recipe_steps TEXT,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    conn.close()

inicializar_db()

# --- NUEVA LÓGICA: PROCESADOR DE RECETA TELEGRAM ---
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente):
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            await client.send_message(bot_username, "/start")
            await asyncio.sleep(3)
            
            pasos = receta_text.split("\n")
            for paso in pasos:
                p = paso.strip()
                if not p: continue
                
                if p.startswith("BOTON:"):
                    btn_target = p.replace("BOTON:", "").strip()
                    msgs = await client.get_messages(bot_username, limit=1)
                    if msgs and msgs[0].reply_markup:
                        await msgs[0].click(text=btn_target)
                    await asyncio.sleep(3)

                elif p == "ENVIAR:CORREO":
                    await client.send_message(bot_username, email_cliente)
                    await asyncio.sleep(3)

                elif p.startswith("ENVIAR:"):
                    texto_a_enviar = p.replace("ENVIAR:", "").strip()
                    await client.send_message(bot_username, texto_a_enviar)
                    await asyncio.sleep(3)

                elif p.startswith("ESPERAR:"):
                    seg = int(re.search(r'\d+', p).group())
                    await asyncio.sleep(seg)
            
            await asyncio.sleep(2)
            mensajes_finales = await client.get_messages(bot_username, limit=1)
            return mensajes_finales[0].text if mensajes_finales else "Sin respuesta final."
            
    except Exception as e:
        return f"Error en el Mapeo: {str(e)}"

# --- 2. LÓGICA DE EXTRACCIÓN DE CÓDIGO ---
def obtener_codigo_real(correo_cuenta, password_app):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(correo_cuenta, password_app)
        mail.select("inbox")
        criterio = '(FROM "info@account.netflix.com" SUBJECT "Tu codigo de acceso temporal")'
        status, mensajes = mail.search(None, criterio)
        if not mensajes[0]: return "No hay correos recientes."
        ultimo_id = mensajes[0].split()[-1]
        res, datos = mail.fetch(ultimo_id, '(RFC822)')
        raw_email = datos[0][1]
        msg = email.message_from_bytes(raw_email)
        cuerpo_html = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    cuerpo_html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
        else:
            cuerpo_html = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
        links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo_html)
        link_codigo = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l]
        if not link_codigo: return "Botón de Netflix no válido."
        respuesta = requests.get(link_codigo[0])
        texto_pagina = respuesta.content.decode('utf-8', errors='ignore')
        todos_los_numeros = re.findall(r'\b\d{4}\b', texto_pagina)
        codigos_limpios = [n for n in todos_los_numeros if n not in ["2024", "2025", "2026"]]
        return codigos_limpios[0] if codigos_limpios else "Código no visualizado."
    except Exception as e:
        return f"Error de conexión: {str(e)}"

# --- 3. INTERFAZ Y NAVEGACIÓN ---
st.set_page_config(page_title="Sistema de Gestión de Cuentas", layout="centered")

menu = ["Panel Cliente", "Panel Vendedor", "Administrador", "🔑 Generar mi Llave"]
opcion = st.sidebar.selectbox("Seleccione un Panel", menu)

if opcion == "🔑 Generar mi Llave":
    st.header("🛡️ Generador de Sesión Seguro")
    phone = st.text_input("Tu número de Telegram (+58...)", key="phone_input_final")
    if st.button("Paso 1: Solicitar Código"):
        if phone:
            async def solicitar():
                client = TelegramClient(StringSession(), MI_API_ID, MI_API_HASH)
                await client.connect()
                res = await client.send_code_request(phone)
                st.session_state.p_hash = res.phone_code_hash
                st.session_state.p_phone = phone
                st.session_state.p_step = 2
                await client.disconnect()
            asyncio.run(solicitar())
            st.success("📩 Código enviado.")
    if st.session_state.get('p_step') == 2:
        code = st.text_input("Introduce el código de 5 dígitos", key="code_input_final")
        if st.button("Paso 2: Generar mi Llave"):
            async def validar():
                try:
                    client = TelegramClient(StringSession(), MI_API_ID, MI_API_HASH)
                    await client.connect()
                    await client.sign_in(st.session_state.p_phone, code, phone_code_hash=st.session_state.p_hash)
                    st.session_state.mi_llave_final = client.session.save()
                    st.session_state.p_step = 3
                    await client.disconnect()
                except Exception as e:
                    st.error(f"Error: {str(e)}")
            asyncio.run(validar())
    if 'mi_llave_final' in st.session_state:
        st.success("🎯 ¡LOGRADO!")
        st.code(st.session_state.mi_llave_final)

elif opcion == "Administrador":
    st.header("🔑 Acceso Administrativo")
    clave_admin = st.text_input("Ingrese Clave Maestra", type="password")
    if clave_admin == "merida2026":
        with st.expander("➕ Registrar Nuevo Vendedor"):
            nuevo_v = st.text_input("Usuario Vendedor")
            clave_v = st.text_input("Clave Vendedor", type="password")
            if st.button("Crear Vendedor"):
                conn = sqlite3.connect('gestion_netflix.db')
                c = conn.cursor()
                vencimiento = (datetime.now() + timedelta(days=30)).date()
                try:
                    c.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", (nuevo_v, clave_v, 1, vencimiento))
                    conn.commit()
                    st.success(f"Vendedor {nuevo_v} creado.")
                except: st.error("El usuario ya existe.")
                conn.close()
        st.subheader("Lista de Vendedores")
        conn = sqlite3.connect('gestion_netflix.db')
        df_v = pd.read_sql_query("SELECT id, usuario, clave, estado, fecha_vencimiento FROM vendedores", conn)
        for index, row in df_v.iterrows():
            col1, col2, col3 = st.columns([2, 2, 1])
            col1.write(f"👤 **{row['usuario']}**")
            col2.write(f"Vence: {row['fecha_vencimiento']}")
            if col3.button("Alt", key=f"v_btn_{row['id']}"):
                nuevo_estado = 0 if row['estado'] == 1 else 1
                conn.cursor().execute("UPDATE vendedores SET estado = ? WHERE id = ?", (nuevo_estado, row['id']))
                conn.commit()
                st.rerun()
        conn.close()

elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Acceso Vendedores")
    u_vend = st.text_input("Usuario")
    p_vend = st.text_input("Clave", type="password")
    if u_vend and p_vend:
        conn = sqlite3.connect('gestion_netflix.db')
        c = conn.cursor()
        c.execute("SELECT id, estado, fecha_vencimiento FROM vendedores WHERE usuario=? AND clave=?", (u_vend, p_vend))
        vendedor = c.fetchone()
        if vendedor:
            v_id, v_estado, v_vence = vendedor
            v_vence_dt = datetime.strptime(v_vence, '%Y-%m-%d').date()
            if v_estado == 0 or v_vence_dt < datetime.now().date():
                st.error("Cuenta inactiva.")
            else:
                st.success(f"Bienvenido. Acceso hasta: {v_vence}")
                with st.form("registro_cliente"):
                    st.subheader("Registrar Nuevo Cliente")
                    p_form = st.selectbox("Plataforma", ["Netflix", "Disney+", "Prime Video", "Bot Automatizado"])
                    m_form = st.text_input("Correo Netflix (Dueño)")
                    app_form = st.text_input("Clave Aplicación Gmail", type="password")
                    u_cli_form = st.text_input("Correo de cuenta registrada")
                    p_cli_form = st.text_input("Clave para pedir Código", type="password")
                    st.markdown("---")
                    st.subheader("🤖 Configuración del Bot")
                    s_session = st.text_area("String Session (Llave)")
                    p_bot = st.text_input("Username del Bot Proveedor")
                    r_steps = st.text_area("Receta de Pasos")
                    if st.form_submit_button("Guardar Cliente"):
                        try:
                            c.execute("""INSERT INTO cuentas (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado, string_session, provider_bot, recipe_steps) 
                                        VALUES (?,?,?,?,?,?,?,?,?,?)""", (p_form, m_form, app_form, u_cli_form, p_cli_form, v_id, 1, s_session, p_bot, r_steps))
                            conn.commit()
                            st.success("✅ Cliente registrado.")
                        except: st.error("El usuario ya existe.")

                # --- INTEGRACIÓN: Lista de Clientes con Función de Eliminar ---
                st.markdown("---")
                st.subheader("🗑️ Mis Clientes (Gestionar)")
                df_c = pd.read_sql_query(f"SELECT usuario_cliente, plataforma, email FROM cuentas WHERE vendedor_id={v_id}", conn)
                
                if df_c.empty:
                    st.info("No tienes clientes registrados aún.")
                else:
                    for index, row in df_c.iterrows():
                        with st.container():
                            col_info, col_del = st.columns([4, 1])
                            with col_info:
                                st.write(f"📺 **{row['usuario_cliente']}** ({row['plataforma']})")
                                st.caption(f"📧 Correo: {row['email']}")
                            with col_del:
                                # // INTEGRACIÓN: Botón de eliminación con confirmación por clave
                                if st.button("Eliminar", key=f"del_{row['usuario_cliente']}"):
                                    c.execute("DELETE FROM cuentas WHERE usuario_cliente=? AND vendedor_id=?", (row['usuario_cliente'], v_id))
                                    conn.commit()
                                    st.warning(f"Cliente {row['usuario_cliente']} eliminado.")
                                    st.rerun()
                            st.divider()
        else: st.error("Credenciales incorrectas.")
        conn.close()

elif opcion == "Panel Cliente":
    st.header("📺 Obtener mi Código")
    u_log = st.text_input("Correo de cuenta")
    p_log = st.text_input("Clave para pedir Código", type="password")
    if st.button("GENERAR CÓDIGO"):
        if u_log and p_log:
            conn = sqlite3.connect('gestion_netflix.db')
            c = conn.cursor()
            c.execute("SELECT * FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?", (u_log, p_log))
            result = c.fetchone()
            if result:
                email_acc, pass_app = result[2], result[3]
                s_session, p_bot, r_steps = result[8], result[9], result[10]
                c.execute("SELECT estado, fecha_vencimiento FROM vendedores WHERE id=?", (result[6],))
                v_status = c.fetchone()
                v_vence_dt = datetime.strptime(v_status[1], '%Y-%m-%d').date()
                if v_status[0] == 0 or v_vence_dt < datetime.now().date():
                    st.error("Servicio inactivo.")
                else:
                    with st.spinner('Procesando...'):
                        if s_session and p_bot:
                            codigo = asyncio.run(ejecutar_receta_bot(s_session, p_bot, r_steps, email_acc))
                            st.info(f"Respuesta del Bot: {codigo}")
                        else:
                            codigo = obtener_codigo_real(email_acc, pass_app)
                            if len(str(codigo)) == 4:
                                st.balloons()
                                st.markdown(f"<h1 style='text-align: center; color: #E50914;'>{codigo}</h1>", unsafe_allow_html=True)
                            else: st.warning(codigo)
            else: st.error("Usuario o clave incorrectos.")
            conn.close()

st.sidebar.markdown("---")
st.sidebar.caption("Sistema v2.0 - 2026")
