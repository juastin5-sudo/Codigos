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

# --- 1. CONFIGURACIÓN ---
# // INTEGRACIÓN: Constantes globales preservadas para el uso de la API de Telethon
MI_API_ID = 34062718  
MI_API_HASH = 'ca9d5cbc6ce832c6660f949a5567a159'

def inicializar_db():
    conn = sqlite3.connect('gestion_netflix.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT UNIQUE, clave TEXT, estado INTEGER, fecha_vencimiento DATE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, plataforma TEXT, email TEXT, password_app TEXT, usuario_cliente TEXT UNIQUE, 
                  pass_cliente TEXT, vendedor_id INTEGER, estado INTEGER, string_session TEXT, provider_bot TEXT, recipe_steps TEXT,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    conn.close()

inicializar_db()

# --- MOTOR DE MAPEO CON LOGS DE DIAGNÓSTICO ---
# // INTEGRACIÓN: Función refactorizada para soportar modo_test y generación de trazas (logs)
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):
    logs = []
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            pasos = receta_text.split("\n")
            for paso in pasos:
                p = paso.strip()
                if not p: continue 
                
                # REGLA: Manejo de clics en botones con búsqueda flexible (search=True)
                if p.startswith("BOTON:"):
                    btn_target = p.replace("BOTON:", "").strip()
                    logs.append(f"🔍 Buscando botón: {btn_target}")
                    await asyncio.sleep(2)
                    msgs = await client.get_messages(bot_username, limit=1)
                    if msgs and msgs[0].reply_markup:
                        exito = await msgs[0].click(text=btn_target, search=True)
                        logs.append("✅ Clic exitoso" if exito else f"❌ Botón '{btn_target}' no encontrado")
                    await asyncio.sleep(2)

                # REGLA: Envío de correo electrónico parametrizado
                elif p == "ENVIAR:CORREO":
                    logs.append(f"📧 Enviando correo: {email_cliente}")
                    await client.send_message(bot_username, email_cliente)
                    await asyncio.sleep(2)

                # REGLA: Envío de comandos o texto manual
                elif p.startswith("ENVIAR:"):
                    texto = p.replace("ENVIAR:", "").strip()
                    logs.append(f"⌨️ Enviando texto: {texto}")
                    await client.send_message(bot_username, texto)
                    await asyncio.sleep(2)

                # REGLA: Pausas controladas para sincronización con el bot
                elif p.startswith("ESPERAR:"):
                    seg = int(re.search(r'\d+', p).group())
                    logs.append(f"⏳ Esperando {seg} segundos...")
                    await asyncio.sleep(seg)
            
            logs.append("📡 Esperando respuesta final con código...")
            res_txt = "Sin respuesta"
            for _ in range(20):
                mensajes = await client.get_messages(bot_username, limit=1)
                if mensajes:
                    res_txt = mensajes[0].text
                    # Filtro para detectar códigos numéricos omitiendo mensajes de "buscando"
                    if re.search(r'\d{4,}', res_txt) and "buscando" not in res_txt.lower():
                        logs.append("🎯 Código detectado correctamente.")
                        return (res_txt, logs) if modo_test else res_txt
                await asyncio.sleep(1)
            
            return (res_txt, logs) if modo_test else res_txt
    except Exception as e:
        error_msg = f"❗ Error: {str(e)}"
        return (error_msg, logs) if modo_test else error_msg

# --- LÓGICA GMAIL (PRESERVADA) ---
def obtener_codigo_real(correo_cuenta, password_app):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(correo_cuenta, password_app)
        mail.select("inbox")
        status, mensajes = mail.search(None, '(FROM "info@account.netflix.com" SUBJECT "Tu codigo de acceso temporal")')
        if not mensajes[0]: return "No hay correos."
        ultimo_id = mensajes[0].split()[-1]
        res, datos = mail.fetch(ultimo_id, '(RFC822)')
        msg = email.message_from_bytes(datos[0][1])
        cuerpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore') if not msg.is_multipart() else ""
        nums = re.findall(r'\b\d{4}\b', cuerpo)
        return nums[0] if nums else "Código no hallado."
    except Exception as e: return f"Error: {str(e)}"

# --- INTERFAZ ---
st.set_page_config(page_title="Gestión Pro 2026", layout="centered")
menu = ["Panel Cliente", "Panel Vendedor", "Administrador", "🔑 Generar mi Llave"]
opcion = st.sidebar.selectbox("Panel", menu)

# --- PANEL GENERADOR ---
if opcion == "🔑 Generar mi Llave":
    st.header("🛡️ Generador de Llave")
    phone = st.text_input("Número (+58...)")
    if st.button("Paso 1: Solicitar"):
        async def sol():
            c = TelegramClient(StringSession(), MI_API_ID, MI_API_HASH)
            await c.connect()
            r = await c.send_code_request(phone)
            st.session_state.update({'p_hash': r.phone_code_hash, 'p_phone': phone, 'p_step': 2, 'active_client': c})
        asyncio.run(sol()); st.success("📩 Código enviado.")
    if st.session_state.get('p_step') == 2:
        code = st.text_input("Código de Telegram")
        if st.button("Paso 2: Generar"):
            async def val():
                try:
                    cl = st.session_state.active_client
                    if not cl.is_connected(): await cl.connect()
                    await cl.sign_in(st.session_state.p_phone, code, phone_code_hash=st.session_state.p_hash)
                    st.session_state.mi_llave_final = cl.session.save()
                    await cl.disconnect()
                except Exception as e: st.error(f"Error: {str(e)}")
            asyncio.run(val()); st.success("🎯 Llave generada.")
    if 'mi_llave_final' in st.session_state: st.code(st.session_state.mi_llave_final)

# --- PANEL ADMINISTRADOR ---
elif opcion == "Administrador":
    st.header("🔑 Admin")
    clave_admin = st.text_input("Ingrese Clave Maestra", type="password")
    if clave_admin == "merida2026":
        st.success("Acceso Concedido")
        with st.expander("Vendedor"):
            u, p = st.text_input("User"), st.text_input("Pass")
            if st.button("Crear"):
                conn = sqlite3.connect('gestion_netflix.db')
                conn.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", 
                             (u, p, 1, (datetime.now()+timedelta(days=30)).date()))
                conn.commit(); conn.close(); st.success("Vendedor creado.")
        conn = sqlite3.connect('gestion_netflix.db')
        df = pd.read_sql_query("SELECT id, usuario, estado FROM vendedores", conn)
        for i, r in df.iterrows():
            c1, c2 = st.columns([3, 1])
            c1.write(f"👤 {r['usuario']}")
            if c2.button("Alt", key=f"v_{r['id']}"):
                conn.execute("UPDATE vendedores SET estado=? WHERE id=?", (0 if r['estado']==1 else 1, r['id']))
                conn.commit(); conn.close(); st.rerun()

# --- PANEL VENDEDOR ---
elif opcion == "Panel Vendedor":
    u_v, p_v = st.text_input("User"), st.text_input("Pass", type="password")
    if u_v and p_v:
        conn = sqlite3.connect('gestion_netflix.db')
        v = conn.execute("SELECT id, estado FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()
        if v and v[1] == 1:
            with st.form("cli"):
                plat = st.selectbox("Plataforma", ["Disney+", "Netflix"])
                em, ca = st.text_input("Email Dueño"), st.text_input("Clave Gmail App")
                uc, pc = st.text_input("User Cliente"), st.text_input("Pass Cliente")
                ss, pb = st.text_area("String Session"), st.text_input("Username del Bot")
                # // INTEGRACIÓN: Placeholder educativo para la receta
                re_steps = st.text_area("Receta", placeholder="ENVIAR:/start\nBOTON:Disney\nENVIAR:CORREO")
                if st.form_submit_button("Guardar Cliente"):
                    conn.execute("""INSERT INTO cuentas (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado, string_session, provider_bot, recipe_steps) 
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""", (plat, em, ca, uc, pc, v[0], 1, ss, pb, re_steps))
                    conn.commit(); st.success("Guardado.")
            
            st.subheader("🗑️ Mis Clientes")
            df_c = pd.read_sql_query(f"SELECT * FROM cuentas WHERE vendedor_id={v[0]}", conn)
            for i, r in df_c.iterrows():
                with st.expander(f"📺 {r['usuario_cliente']}"):
                    col_a, col_b = st.columns(2)
                    # // INTEGRACIÓN: Nueva herramienta de diagnóstico para el vendedor
                    if col_a.button("🧪 Probar Mapeo", key=f"t_{r['usuario_cliente']}"):
                        with st.spinner("Ejecutando diagnóstico..."):
                            res, logs = asyncio.run(ejecutar_receta_bot(r['string_session'], r['provider_bot'], r['recipe_steps'], r['email'], modo_test=True))
                            for log in logs: st.caption(log)
                            st.code(f"Resultado final: {res}")
                    
                    if col_b.button("❌ Eliminar Cliente", key=f"d_{r['usuario_cliente']}"):
                        conn.execute("DELETE FROM cuentas WHERE usuario_cliente=?", (r['usuario_cliente'],))
                        conn.commit(); st.rerun()
        conn.close()

# --- PANEL CLIENTE ---
elif opcion == "Panel Cliente":
    st.header("📺 Obtener Código")
    u_cl, p_cl = st.text_input("Usuario"), st.text_input("Clave", type="password")
    if st.button("GENERAR"):
        conn = sqlite3.connect('gestion_netflix.db')
        res = conn.execute("SELECT * FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?", (u_cl, p_cl)).fetchone()
        if res:
            with st.spinner("Procesando..."):
                if res[8] and res[9]:
                    # // INTEGRACIÓN: Despacho a la lógica del bot con el nuevo motor
                    out = asyncio.run(ejecutar_receta_bot(res[8], res[9], res[10], res[2]))
                    st.info(f"Respuesta: {out}")
                    nums = re.findall(r'\d+', out)
                    if nums: st.balloons(); st.markdown(f"<h1 style='text-align:center; color:#E50914;'>{nums[0]}</h1>", unsafe_allow_html=True)
                else:
                    # Lógica Gmail original preservada
                    cod = obtener_codigo_real(res[2], res[3])
                    st.markdown(f"<h1 style='text-align:center; color:#E50914;'>{cod}</h1>", unsafe_allow_html=True)
        conn.close()

st.sidebar.caption("v2.5 - Diagnostic Tool")
