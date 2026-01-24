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
    
    # // INTEGRACIÓN: Tabla para buzones principales
    c.execute('''CREATE TABLE IF NOT EXISTS correos_madre (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 vendedor_id INTEGER,
                 correo_imap TEXT,
                 password_app TEXT,
                 servidor_imap TEXT DEFAULT 'imap.gmail.com',
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    # // INTEGRACIÓN: Tabla cuentas vinculada
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

# --- NUEVA LÓGICA: MOTOR DE MAPEO CON ESCÁNER DE BOTONES ---
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

# --- 2. LÓGICA DE EXTRACCIÓN CENTRALIZADA ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv="imap.gmail.com"):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        
        if plataforma == "Prime Video":
            criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")'
        else:
            criterio = f'(FROM "info@account.netflix.com" TO "{email_cliente_final}")'
        
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
            patron = r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})'
            match = re.search(patron, cuerpo, re.IGNORECASE)
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

# --- 3. INTERFAZ Y NAVEGACIÓN ---
st.set_page_config(page_title="Sistema de Gestión de Cuentas v2.9", layout="centered")

# // INTEGRACIÓN: Apartado de 'Generar mi Llave' eliminado del menú
menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Seleccione un Panel", menu)

# --- PANEL ADMINISTRADOR ---
if opcion == "Administrador":
    st.header("🔑 Acceso Administrativo")
    clave_admin = st.text_input("Ingrese Clave Maestra", type="password")
    if clave_admin == "merida2026":
        st.success("Acceso Concedido")
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
            with st.container():
                col1, col2, col3 = st.columns([2, 2, 1])
                col1.write(f"👤 **{row['usuario']}**")
                col2.write(f"Vence: {row['fecha_vencimiento']} | {'✅' if row['estado']==1 else '❌'}")
                if col3.button("Alt", key=f"btn_{row['id']}"):
                    nuevo_estado = 0 if row['estado'] == 1 else 1
                    conn.cursor().execute("UPDATE vendedores SET estado = ? WHERE id = ?", (nuevo_estado, row['id']))
                    conn.commit()
                    st.rerun()
                st.divider()
        conn.close()

# --- PANEL VENDEDOR ---
elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Acceso Vendedores")
    u_vend = st.text_input("Usuario")
    p_vend = st.text_input("Clave", type="password")
    
    if u_vend and p_vend:
        conn = sqlite3.connect('gestion_netflix.db')
        c = conn.cursor()
        c.execute("SELECT id, estado FROM vendedores WHERE usuario=? AND clave=?", (u_vend, p_vend))
        vendedor = c.fetchone()
        
        if vendedor and vendedor[1] == 1:
            v_id = vendedor[0]
            
            with st.expander("📧 Gestionar mis Buzones (Correos Madre)"):
                with st.form("form_madre"):
                    m_email = st.text_input("Correo IMAP")
                    m_pass = st.text_input("Clave App", type="password")
                    m_serv = st.text_input("Servidor IMAP", value="imap.gmail.com")
                    if st.form_submit_button("Registrar Buzón Madre"):
                        c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap) VALUES (?,?,?,?)", 
                                  (v_id, m_email, m_pass, m_serv))
                        conn.commit()
                        st.success("Buzón registrado.")

            with st.form("registro_cliente"):
                st.subheader("Registrar/Actualizar Cliente")
                u_cli_form = st.text_input("Correo de cuenta registrada (ID Único)")
                
                c.execute("SELECT id, correo_imap FROM correos_madre WHERE vendedor_id=?", (v_id,))
                madres = c.fetchall()
                opciones_madre = {m[1]: m[0] for m in madres}
                madre_seleccionada = st.selectbox("Asociar a Buzón Madre", options=list(opciones_madre.keys()))
                
                val_actual = st.session_state.get('temp_recipe', "")
                r_steps = st.text_area("Receta de Pasos", value=val_actual, height=150)
                p_form = st.selectbox("Plataforma", ["Netflix", "Disney+", "Prime Video", "Bot Automatizado"])
                p_cli_form = st.text_input("Clave Cliente", type="password")
                s_session = st.text_area("String Session")
                p_bot = st.text_input("Username Bot")

                if st.form_submit_button("Guardar Cliente"):
                    id_m = opciones_madre.get(madre_seleccionada)
                    c.execute("""INSERT OR REPLACE INTO cuentas 
                                 (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado, string_session, provider_bot, recipe_steps, id_madre) 
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""", 
                                 (p_form, u_cli_form, "USAR_MADRE", u_cli_form, p_cli_form, v_id, 1, s_session, p_bot, r_steps, id_m))
                    conn.commit()
                    st.success("✅ Datos guardados correctamente.")
                    st.session_state['temp_recipe'] = "" 

            st.markdown("---")
            df_c = pd.read_sql_query(f"SELECT * FROM cuentas WHERE vendedor_id={v_id}", conn)
            for _, row in df_c.iterrows():
                with st.expander(f"📺 {row['usuario_cliente']} ({row['plataforma']})"):
                    c1, c2 = st.columns(2)
                    if c1.button("🧪 ESCANEAR BOT", key=f"scan_act_{row['id']}"):
                        with st.spinner("Conectando con Telegram..."):
                            res, logs, botones = asyncio.run(ejecutar_receta_bot(row['string_session'], row['provider_bot'], row['recipe_steps'], row['email'], modo_test=True))
                            st.session_state[f"last_scan_{row['id']}"] = (res, logs, botones)

                    scan_data = st.session_state.get(f"last_scan_{row['id']}")
                    if scan_data:
                        res, logs, botones = scan_data
                        for l in logs: st.caption(l)
                        if botones:
                            st.write("### 🤖 Botones Detectados:")
                            cols_btn = st.columns(3)
                            for idx, btn_txt in enumerate(botones):
                                if cols_btn[idx % 3].button(f"➕ {btn_txt}", key=f"add_{row['id']}_{idx}"):
                                    nueva_linea = f"BOTON:{btn_txt}"
                                    nueva_receta = (str(row['recipe_steps']) + "\n" + nueva_linea).strip()
                                    c.execute("UPDATE cuentas SET recipe_steps=? WHERE id=?", (nueva_receta, row['id']))
                                    conn.commit()
                                    st.session_state['temp_recipe'] = nueva_receta
                                    st.rerun()
                        st.info(f"Respuesta: {res}")

                    if c2.button("Eliminar", key=f"del_v_{row['id']}"):
                        c.execute("DELETE FROM cuentas WHERE id=?", (row['id'],))
                        conn.commit()
                        st.rerun()
        conn.close()

# --- PANEL CLIENTE ---
elif opcion == "Panel Cliente":
    st.header("📺 Obtener mi Código")
    u_log = st.text_input("Correo de cuenta")
    p_log = st.text_input("Clave para pedir Código", type="password")
    if st.button("GENERAR CÓDIGO"):
        if u_log and p_log:
            conn = sqlite3.connect('gestion_netflix.db')
            c = conn.cursor()
            c.execute("SELECT * FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?" , (u_log, p_log))
            result = c.fetchone()
            if result:
                v_id_ref = result[6]
                id_madre_ref = result[11]
                
                c.execute("SELECT estado, fecha_vencimiento FROM vendedores WHERE id=?", (v_id_ref,))
                v_status = c.fetchone()
                
                if v_status[0] == 0 or datetime.strptime(v_status[1], '%Y-%m-%d').date() < datetime.now().date():
                    st.error("Servicio inactivo.")
                else:
                    with st.spinner('Procesando...'):
                        if result[8] and result[9]: 
                            codigo = asyncio.run(ejecutar_receta_bot(result[8], result[9], result[10], result[2]))
                            st.info(f"Respuesta del Bot: {codigo}")
                        else:
                            c.execute("SELECT correo_imap, password_app, servidor_imap FROM correos_madre WHERE id=?", (id_madre_ref,))
                            datos_madre = c.fetchone()
                            
                            if datos_madre:
                                codigo = obtener_codigo_centralizado(
                                    email_madre=datos_madre[0], 
                                    pass_app_madre=datos_madre[1], 
                                    email_cliente_final=result[2], 
                                    plataforma=result[1],
                                    imap_serv=datos_madre[2]
                                )
                                
                                if str(codigo).isdigit() and len(str(codigo)) in [4, 6]:
                                    st.balloons()
                                    st.markdown(f"<h1 style='text-align: center; color: #E50914;'>{codigo}</h1>", unsafe_allow_html=True)
                                else: 
                                    st.warning(codigo)
                            else:
                                st.error("No se encontró buzón configurado para esta cuenta.")
            else: st.error("Usuario o clave incorrectos.")
            conn.close()

st.sidebar.markdown("---")
st.sidebar.caption("Sistema v2.9 - Clean Architecture 2026")
