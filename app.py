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

# --- 1. CONFIGURACIÓN DE BASE DE DATOS (Original) ---
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

# --- NUEVA LÓGICA: MOTOR DE MAPEO CON ESCÁNER DE BOTONES (Integrado) ---
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):
    logs = []
    botones_finales = [] # // INTEGRACIÓN: Lista para capturar botones interactivos
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            # Paso inicial por defecto
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
                        # // INTEGRACIÓN: clic con búsqueda de texto habilitada
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
            
            # // INTEGRACIÓN: Escáner de botones finales para el modo interactivo
            await asyncio.sleep(2)
            ultimos_msgs = await client.get_messages(bot_username, limit=1)
            if ultimos_msgs and ultimos_msgs[0].reply_markup:
                for row in ultimos_msgs[0].reply_markup.rows:
                    for button in row.buttons:
                        botones_finales.append(button.text)
            
            respuesta = ultimos_msgs[0].text if ultimos_msgs else "Sin respuesta."
            
            # // INTEGRACIÓN: Retorno extendido para el Panel Vendedor
            return (respuesta, logs, botones_finales) if modo_test else respuesta
            
    except Exception as e:
        error_msg = f"Error en el Mapeo: {str(e)}"
        return (error_msg, logs, []) if modo_test else error_msg

# --- 2. LÓGICA DE EXTRACCIÓN DE CÓDIGO (Original Restaurada y Robusta) ---
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
        # // INTEGRACIÓN: Se mantiene el filtrado de años crítico del original
        codigos_limpios = [n for n in todos_los_numeros if n not in ["2024", "2025", "2026"]]
        return codigos_limpios[0] if codigos_limpios else "Código no visualizado."
    except Exception as e:
        return f"Error de conexión: {str(e)}"

# --- 3. INTERFAZ Y NAVEGACIÓN ---
st.set_page_config(page_title="Sistema de Gestión de Cuentas v2.7", layout="centered")

menu = ["Panel Cliente", "Panel Vendedor", "Administrador", "🔑 Generar mi Llave"]
opcion = st.sidebar.selectbox("Seleccione un Panel", menu)

# --- PANEL GENERADOR SEGURO (Original) ---
if opcion == "🔑 Generar mi Llave":
    st.header("🛡️ Generador de Sesión Seguro")
    phone = st.text_input("Número de Telegram (+58...)", key="phone_gen")
    
    if st.button("1. Solicitar Código"):
        if phone:
            async def iniciar_solicitud():
                client = TelegramClient(StringSession(), MI_API_ID, MI_API_HASH)
                await client.connect()
                res = await client.send_code_request(phone)
                st.session_state.p_hash = res.phone_code_hash
                st.session_state.p_number = phone
                st.session_state.wait_code = True
                st.session_state.active_client = client 
            asyncio.run(iniciar_solicitud())
            st.success("✅ Código enviado.")

    if st.session_state.get('wait_code'):
        st.markdown("---")
        v_code = st.text_input("Escribe el código de 5 dígitos", key="v_code_input")
        if st.button("2. ¡Generar Llave Final!"):
            async def completar_registro():
                try:
                    client = st.session_state.active_client
                    if not client.is_connected(): await client.connect()
                    await client.sign_in(st.session_state.p_number, v_code, phone_code_hash=st.session_state.p_hash)
                    st.session_state.final_str = client.session.save()
                    st.session_state.wait_code = False
                    await client.disconnect()
                except Exception as e:
                    st.error(f"Error: {str(e)}")
            asyncio.run(completar_registro())

    if 'final_str' in st.session_state:
        st.balloons()
        st.success("🎯 ¡SESIÓN GENERADA!")
        st.code(st.session_state.final_str)

# --- PANEL ADMINISTRADOR (Original) ---
elif opcion == "Administrador":
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

# --- PANEL VENDEDOR (Original + Inyección de Mapeo Interactivo) ---
# --- PANEL VENDEDOR (REFACCIÓN DE MAPEADOR INTERACTIVO) ---
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
            
            # --- FORMULARIO DE REGISTRO ---
            with st.form("registro_cliente"):
                st.subheader("Registrar/Actualizar Cliente")
                u_cli_form = st.text_input("Correo de cuenta registrada (ID Único)")
                # // INTEGRACIÓN: El área de texto ahora escucha directamente al session_state
                receta_key = f"recipe_input_{u_cli_form}" if u_cli_form else "recipe_input_default"
                
                # Recuperar valor previo si existe en el estado global
                val_actual = st.session_state.get('temp_recipe', "")
                r_steps = st.text_area("Receta de Pasos", value=val_actual, height=150)
                
                # Otros campos...
                p_form = st.selectbox("Plataforma", ["Netflix", "Disney+", "Prime Video", "Bot Automatizado"])
                m_form = st.text_input("Correo Dueño (Gmail)")
                app_form = st.text_input("Clave App Gmail", type="password")
                p_cli_form = st.text_input("Clave Cliente", type="password")
                s_session = st.text_area("String Session")
                p_bot = st.text_input("Username Bot")

                if st.form_submit_button("Guardar Cliente"):
                    c.execute("""INSERT OR REPLACE INTO cuentas 
                                 (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado, string_session, provider_bot, recipe_steps) 
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""", 
                                 (p_form, m_form, app_form, u_cli_form, p_cli_form, v_id, 1, s_session, p_bot, r_steps))
                    conn.commit()
                    st.success("✅ Datos guardados correctamente.")
                    st.session_state['temp_recipe'] = "" # Limpiar después de guardar

            # --- GESTIÓN Y MAPEADOR ---
            st.markdown("---")
            df_c = pd.read_sql_query(f"SELECT * FROM cuentas WHERE vendedor_id={v_id}", conn)
            for _, row in df_c.iterrows():
                with st.expander(f"📺 {row['usuario_cliente']}"):
                    c1, c2 = st.columns(2)
                    
                    if c1.button("🧪 ESCANEAR BOT", key=f"scan_act_{row['id']}"):
                        with st.spinner("Conectando con Telegram..."):
                            # // INTEGRACIÓN: Ejecutamos con la receta actual del registro
                            res, logs, botones = asyncio.run(ejecutar_receta_bot(row['string_session'], row['provider_bot'], row['recipe_steps'], row['email'], modo_test=True))
                            
                            # Guardar resultados en el estado para que sobrevivan al próximo clic de botón
                            st.session_state[f"last_scan_{row['id']}"] = (res, logs, botones)

                    # Mostrar botones si existen en el estado de este cliente
                    scan_data = st.session_state.get(f"last_scan_{row['id']}")
                    if scan_data:
                        res, logs, botones = scan_data
                        for l in logs: st.caption(l)
                        
                        if botones:
                            st.write("### 🤖 Botones Detectados:")
                            cols_btn = st.columns(3)
                            for idx, btn_txt in enumerate(botones):
                                # // INTEGRACIÓN CRÍTICA: Al hacer clic, inyectamos directamente en el estado global
                                if cols_btn[idx % 3].button(f"➕ {btn_txt}", key=f"add_{row['id']}_{idx}"):
                                    nueva_linea = f"BOTON:{btn_txt}"
                                    receta_vieja = row['recipe_steps']
                                    
                                    # Actualizar base de datos inmediatamente para que se vea en el formulario
                                    nueva_receta = (receta_vieja + "\n" + nueva_linea).strip()
                                    c.execute("UPDATE cuentas SET recipe_steps=? WHERE id=?", (nueva_receta, row['id']))
                                    conn.commit()
                                    
                                    # Sincronizar con el formulario de arriba
                                    st.session_state['temp_recipe'] = nueva_receta
                                    st.success(f"Añadido: {btn_txt}")
                                    st.rerun()
                            
                        st.info(f"Respuesta: {res}")

                    if c2.button("Eliminar", key=f"del_v_{row['id']}"):
                        c.execute("DELETE FROM cuentas WHERE id=?", (row['id'],))
                        conn.commit()
                        st.rerun()
        conn.close()

# --- PANEL CLIENTE (Original Restaurado) ---
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
                email_acc, pass_app = result[2], result[3]
                s_session, p_bot, r_steps = result[8], result[9], result[10]
                
                c.execute("SELECT estado, fecha_vencimiento FROM vendedores WHERE id=?", (result[6],))
                v_status = c.fetchone()
                if v_status[0] == 0 or datetime.strptime(v_status[1], '%Y-%m-%d').date() < datetime.now().date():
                    st.error("Servicio inactivo.")
                else:
                    with st.spinner('Procesando...'):
                        if s_session and p_bot:
                            # // INTEGRACIÓN: Llamada compatible con la nueva firma de función
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
st.sidebar.caption("Sistema v2.7 - Interactive Mapper 2026")

