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

# --- INTEGRACIÓN: Constantes Globales preservadas ---
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
    
    c.execute('''CREATE TABLE IF NOT EXISTS correos_madre (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 vendedor_id INTEGER,
                 correo_imap TEXT,
                 password_app TEXT,
                 servidor_imap TEXT DEFAULT 'imap.gmail.com',
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

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
                  id_madre INTEGER,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id),
                  FOREIGN KEY(id_madre) REFERENCES correos_madre(id))''')
    conn.commit()
    conn.close()

inicializar_db()

# --- LÓGICA DE EXTRACCIÓN: BOT DE TELEGRAM ---
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):
    logs = []
    botones_finales = []
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            await client.send_message(bot_username, "/start")
            logs.append("⌨️ Enviado: /start")
            await asyncio.sleep(3) 
            
            pasos = receta_text.split("\n")
            for paso in pasos:
                p = paso.strip()
                if not p: continue 
                if p.startswith("BOTON:"):
                    btn_target = p.replace("BOTON:", "").strip()
                    logs.append(f"🔍 Buscando botón: {btn_target}")
                    msgs = await client.get_messages(bot_username, limit=1)
                    if msgs and msgs[0].reply_markup:
                        exito = await msgs[0].click(text=btn_target, search=True)
                        logs.append("✅ Clic exitoso" if exito else f"❌ Botón '{btn_target}' no encontrado")
                    await asyncio.sleep(3)
                elif p == "ENVIAR:CORREO":
                    logs.append(f"📧 Enviando correo: {email_cliente}")
                    await client.send_message(bot_username, email_cliente)
                    await asyncio.sleep(3)
                elif p.startswith("ENVIAR:"):
                    texto_a_enviar = p.replace("ENVIAR:", "").strip()
                    logs.append(f"⌨️ Enviando texto: {texto_a_enviar}")
                    await client.send_message(bot_username, texto_a_enviar)
                    await asyncio.sleep(3)
                elif p.startswith("ESPERAR:"):
                    seg = int(re.search(r'\d+', p).group())
                    logs.append(f"⏳ Esperando {seg} segundos...")
                    await asyncio.sleep(seg)
            
            await asyncio.sleep(2)
            ultimos_msgs = await client.get_messages(bot_username, limit=1)
            if ultimos_msgs and ultimos_msgs[0].reply_markup:
                for row in ultimos_msgs[0].reply_markup.rows:
                    for button in row.buttons:
                        botones_finales.append(button.text)
            
            respuesta = ultimos_msgs[0].text if ultimos_msgs else "Sin respuesta."
            return (respuesta, logs, botones_finales) if modo_test else respuesta
    except Exception as e:
        error_msg = f"Error en el Mapeo: {str(e)}"
        return (error_msg, logs, []) if modo_test else error_msg

# --- LÓGICA DE EXTRACCIÓN: CORREOS (IMAP) ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv="imap.gmail.com"):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")' if plataforma == "Prime Video" else f'(FROM "info@account.netflix.com" TO "{email_cliente_final}")'
        status, mensajes = mail.search(None, criterio)
        if not mensajes[0]: return f"Correo no hallado para {email_cliente_final}"
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

        if plataforma == "Prime Video":
            match = re.search(r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})', cuerpo, re.IGNORECASE)
            return match.group(1) if match else "Código Prime no detectado"
        else:
            links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo)
            link_n = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l]
            if not link_n: return "Link de Netflix no encontrado"
            resp = requests.get(link_n[0])
            nums = [n for n in re.findall(r'\b\d{4}\b', resp.text) if n not in ["2024", "2025", "2026"]]
            return nums[0] if nums else "Código Netflix no hallado"
    except Exception as e:
        return f"Error: {str(e)}"

# --- INTERFAZ ---
st.set_page_config(page_title="Gestión de Cuentas v3.0", layout="centered")
menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Panel", menu)

if opcion == "Administrador":
    st.header("🔑 Administrador")
    if st.text_input("Clave Maestra", type="password") == "merida2026":
        with st.expander("➕ Nuevo Vendedor"):
            nv, cv = st.text_input("Usuario"), st.text_input("Clave", type="password")
            if st.button("Crear"):
                conn = sqlite3.connect('gestion_netflix.db')
                try:
                    conn.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", (nv, cv, 1, (datetime.now() + timedelta(days=30)).date()))
                    conn.commit()
                    st.success("Creado")
                except: st.error("Existe")
                conn.close()

elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Vendedores")
    u_v, p_v = st.text_input("Usuario"), st.text_input("Clave", type="password")
    if u_v and p_v:
        conn = sqlite3.connect('gestion_netflix.db')
        c = conn.cursor()
        c.execute("SELECT id, estado FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v))
        vend = c.fetchone()
        if vend and vend[1] == 1:
            v_id = vend[0]
            
            with st.expander("📧 Configurar Buzones Madre (Solo para método Correo)"):
                with st.form("f_madre"):
                    me, mp, ms = st.text_input("Correo IMAP"), st.text_input("Clave App", type="password"), st.text_input("Servidor", value="imap.gmail.com")
                    if st.form_submit_button("Guardar Buzón"):
                        c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap) VALUES (?,?,?,?)", (v_id, me, mp, ms))
                        conn.commit()

            # // INTEGRACIÓN: Separación lógica del Registro
            st.subheader("Registrar Nuevo Cliente")
            metodo = st.radio("Método de Extracción:", ["Buzón Madre (Correo)", "Bot de Telegram"], horizontal=True)
            
            with st.form("f_cliente"):
                u_cli = st.text_input("Correo de la Cuenta (Netflix/Disney/etc)")
                p_cli = st.text_input("Clave para el Cliente", type="password")
                plat = st.selectbox("Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
                
                # Campos condicionales según el método
                id_m, s_sess, p_bot, r_steps = None, None, None, None
                
                if metodo == "Buzón Madre (Correo)":
                    c.execute("SELECT id, correo_imap FROM correos_madre WHERE vendedor_id=?", (v_id,))
                    madres = c.fetchall()
                    op_madre = {m[1]: m[0] for m in madres}
                    m_sel = st.selectbox("Selecciona el Buzón Madre donde llega el correo", options=list(op_madre.keys()))
                    id_m = op_madre.get(m_sel)
                else:
                    s_sess = st.text_area("String Session (Llave)")
                    p_bot = st.text_input("Username del Bot (@ejemplo_bot)")
                    val_rec = st.session_state.get('temp_recipe', "")
                    r_steps = st.text_area("Receta de Pasos", value=val_rec)

                if st.form_submit_button("Guardar Cliente"):
                    c.execute("""INSERT OR REPLACE INTO cuentas 
                                 (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado, string_session, provider_bot, recipe_steps, id_madre) 
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""", 
                                 (plat, u_cli, "EXTRACCION_ACTIVA", u_cli, p_cli, v_id, 1, s_sess, p_bot, r_steps, id_m))
                    conn.commit()
                    st.success("Guardado")
            
            # Gestión de cuentas existentes
            st.markdown("---")
            df_c = pd.read_sql_query(f"SELECT * FROM cuentas WHERE vendedor_id={v_id}", conn)
            for _, row in df_c.iterrows():
                with st.expander(f"📺 {row['usuario_cliente']} [{ 'BOT' if row['provider_bot'] else 'CORREO' }]"):
                    if row['provider_bot'] and st.button("🧪 Escanear Bot", key=f"sc_{row['id']}"):
                        res, logs, btns = asyncio.run(ejecutar_receta_bot(row['string_session'], row['provider_bot'], row['recipe_steps'], row['email'], True))
                        for l in logs: st.caption(l)
                        st.info(res)
                    if st.button("Eliminar", key=f"del_{row['id']}"):
                        c.execute("DELETE FROM cuentas WHERE id=?", (row['id'],))
                        conn.commit()
                        st.rerun()
        conn.close()

elif opcion == "Panel Cliente":
    st.header("📺 Obtener mi Código")
    u_l, p_l = st.text_input("Correo de cuenta"), st.text_input("Clave", type="password")
    if st.button("GENERAR CÓDIGO"):
        conn = sqlite3.connect('gestion_netflix.db')
        c = conn.cursor()
        c.execute("SELECT * FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?" , (u_l, p_l))
        res = c.fetchone()
        if res:
            # res[8] es string_session, res[11] es id_madre
            with st.spinner('Extrayendo...'):
                if res[8]: # Método BOT
                    codigo = asyncio.run(ejecutar_receta_bot(res[8], res[9], res[10], res[2]))
                    st.info(f"Respuesta: {codigo}")
                elif res[11]: # Método CORREO
                    c.execute("SELECT correo_imap, password_app, servidor_imap FROM correos_madre WHERE id=?", (res[11],))
                    dm = c.fetchone()
                    codigo = obtener_codigo_centralizado(dm[0], dm[1], res[2], res[1], dm[2])
                    if str(codigo).isdigit():
                        st.balloons()
                        st.markdown(f"<h1 style='text-align: center; color: #E50914;'>{codigo}</h1>", unsafe_allow_html=True)
                    else: st.warning(codigo)
        else: st.error("Datos incorrectos")
        conn.close()
