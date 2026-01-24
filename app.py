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

# --- 1. CONFIGURACIÓN DE BASE DE DATOS ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix.db')
    c = conn.cursor()
    # Tabla Vendedores (Original)
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  usuario TEXT UNIQUE, 
                  clave TEXT, 
                  estado INTEGER, 
                  fecha_vencimiento DATE)''')
    
    # Tabla Cuentas (Extendida con los 3 campos nuevos)
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
    # INTEGRACIÓN: Credenciales base del sistema
    api_id = 34062718  
    api_hash = 'ca9d5cbc6ce832c6660f949a5567a159'
    
    try:
        async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
            await client.send_message(bot_username, "/start")
            await asyncio.sleep(2)
            
            pasos = receta_text.split("\n")
            for paso in pasos:
                p = paso.strip()
                if p.startswith("BOTON:"):
                    btn_text = p.replace("BOTON:", "").strip()
                    msgs = await client.get_messages(bot_username, limit=1)
                    if msgs and msgs[0].reply_markup:
                        await msgs[0].click(text=btn_text)
                elif p.startswith("ENVIAR:CORREO"):
                    await client.send_message(bot_username, email_cliente)
                elif p.startswith("ESPERAR:"):
                    seg = int(re.search(r'\d+', p).group())
                    await asyncio.sleep(seg)
            
            await asyncio.sleep(2)
            final_msg = await client.get_messages(bot_username, limit=1)
            return final_msg[0].text
    except Exception as e:
        return f"Error Automatización: {str(e)}"

# --- 2. LÓGICA DE EXTRACCIÓN DE CÓDIGO (ORIGINAL INTACTA) ---
def obtener_codigo_real(correo_cuenta, password_app):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(correo_cuenta, password_app)
        mail.select("inbox")
        
        criterio = '(FROM "info@account.netflix.com" SUBJECT "Tu codigo de acceso temporal")'
        status, mensajes = mail.search(None, criterio)
        
        if not mensajes[0]: 
            return "No hay correos recientes. Solicita el código en tu TV primero."
        
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

        if not link_codigo:
            return "Correo encontrado, pero el botón de Netflix no es válido."

        respuesta = requests.get(link_codigo[0])
        texto_pagina = respuesta.content.decode('utf-8', errors='ignore')
        
        todos_los_numeros = re.findall(r'\b\d{4}\b', texto_pagina)
        codigos_limpios = [n for n in todos_los_numeros if n not in ["2024", "2025", "2026"]]
        
        if codigos_limpios:
            return codigos_limpios[0]
        else:
            return "El link abrió pero no se visualizó el código de 4 dígitos."

    except Exception as e:
        return f"Error de conexión: {str(e)}"

# --- 3. INTERFAZ Y NAVEGACIÓN ---
st.set_page_config(page_title="Sistema de Gestión de Cuentas", layout="centered")

menu = ["Panel Cliente", "Panel Vendedor", "Administrador", "🔑 Generar mi Llave"]
opcion = st.sidebar.selectbox("Seleccione un Panel", menu)

# --- INTEGRACIÓN: LÓGICA DEL GENERADOR SEGURO (CONEXIÓN PERSISTENTE) ---
if opcion == "🔑 Generar mi Llave":
    st.header("🛡️ Generador de Sesión Seguro")
    
    # INTEGRACIÓN: Credenciales unificadas para el generador
    api_id = 34062718 
    api_hash = 'ca9d5cbc6ce832c6660f949a5567a159'

    # 1. Entrada de teléfono
    phone = st.text_input("Número (+58...)", key="phone_gen")
    
    if st.button("1. Solicitar Código"):
        if phone:
            async def iniciar_solicitud():
                # INTEGRACIÓN: Se crea el cliente y se guarda en session_state para persistencia
                client = TelegramClient(StringSession(), api_id, api_hash)
                await client.connect()
                res = await client.send_code_request(phone)
                
                # INTEGRACIÓN: Guardado de metadata y objeto cliente activo
                st.session_state.p_hash = res.phone_code_hash
                st.session_state.p_number = phone
                st.session_state.wait_code = True
                st.session_state.active_client = client 
            
            asyncio.run(iniciar_solicitud())
            st.success("✅ Código enviado. ¡Aparecerá en tu Telegram en segundos!")

    # 2. Entrada de código (Solo aparece si el paso 1 tuvo éxito)
    if st.session_state.get('wait_code'):
        st.markdown("---")
        v_code = st.text_input("Escribe el código de 5 dígitos aquí", key="v_code_input")
        
        if st.button("2. ¡Generar Llave Final!"):
            if v_code:
                async def completar_registro():
                    try:
                        # INTEGRACIÓN: Recuperación del cliente persistente
                        client = st.session_state.active_client
                        
                        if not client.is_connected():
                            await client.connect()
                        
                        # Intentamos el login con los datos persistidos
                        await client.sign_in(
                            st.session_state.p_number, 
                            v_code, 
                            phone_code_hash=st.session_state.p_hash
                        )
                        
                        # Generar y guardar la llave en el estado
                        st.session_state.final_str = client.session.save()
                        st.session_state.wait_code = False
                        await client.disconnect() # Cierre seguro tras éxito
                    except Exception as e:
                        st.error(f"Error al validar: {str(e)}")
                        if 'active_client' in st.session_state: 
                            await st.session_state.active_client.disconnect()
                
                asyncio.run(completar_registro())

    # 3. Resultado final persistente
    if 'final_str' in st.session_state:
        st.balloons()
        st.success("🎯 ¡SESIÓN GENERADA!")
        st.code(st.session_state.final_str)
        st.info("Copia el código de arriba y pégalo en el formulario de vendedor.")

# --- PANEL ADMINISTRADOR (INTACTO) ---
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
                    c.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", 
                              (nuevo_v, clave_v, 1, vencimiento))
                    conn.commit()
                    st.success(f"Vendedor {nuevo_v} creado hasta {vencimiento}")
                except:
                    st.error("El usuario ya existe.")
                conn.close()

        st.subheader("Lista de Vendedores")
        conn = sqlite3.connect('gestion_netflix.db')
        df_v = pd.read_sql_query("SELECT id, usuario, clave, estado, fecha_vencimiento FROM vendedores", conn)
        
        for index, row in df_v.iterrows():
            with st.container():
                col1, col2, col3 = st.columns([2, 2, 1])
                with col1:
                    st.write(f"👤 **{row['usuario']}**")
                    st.caption(f"🔑 Clave: {row['clave']}")
                with col2:
                    estado_txt = "✅ Activo" if row['estado'] == 1 else "❌ Suspendido"
                    st.write(f"Vence: {row['fecha_vencimiento']}")
                    st.write(f"Estado: {estado_txt}")
                with col3:
                    if st.button("Alt", key=f"btn_{row['id']}"):
                        nuevo_estado = 0 if row['estado'] == 1 else 1
                        conn.cursor().execute("UPDATE vendedores SET estado = ? WHERE id = ?", (nuevo_estado, row['id']))
                        conn.commit()
                        st.rerun()
                st.markdown("---")
        conn.close()

# --- PANEL VENDEDOR (EXTENDIDO) ---
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
                st.error("Tu cuenta está suspendida o vencida. Contacta al Admin.")
            else:
                st.success(f"Bienvenido. Tu acceso vence el: {v_vence}")
                
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
                    p_bot = st.text_input("Username del Bot Proveedor (ej: @Bot)")
                    r_steps = st.text_area("Receta de Pasos (Uno por línea)", placeholder="BOTON:Generar\nENVIAR:CORREO\nESPERAR:5")
                    
                    if st.form_submit_button("Guardar Cliente"):
                        try:
                            c.execute("""INSERT INTO cuentas 
                                (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado, string_session, provider_bot, recipe_steps) 
                                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                (p_form, m_form, app_form, u_cli_form, p_cli_form, v_id, 1, s_session, p_bot, r_steps))
                            conn.commit()
                            st.success("✅ Cliente y Bot registrados con éxito.")
                        except:
                            st.error("Error: El nombre de usuario del cliente ya existe.")
                
                st.subheader("Mis Clientes")
                df_c = pd.read_sql_query(f"SELECT usuario_cliente, plataforma, email FROM cuentas WHERE vendedor_id={v_id}", conn)
                st.table(df_c)
        else:
            st.error("Credenciales incorrectas.")
        conn.close()

# --- PANEL CLIENTE (EXTENDIDO) ---
elif opcion == "Panel Cliente":
    st.header("📺 Obtener mi Código")
    u_log = st.text_input("Correo de cuenta")
    p_log = st.text_input("Clave para pedir Código", type="password")
    
    if st.button("GENERAR CÓDIGO"):
        if u_log and p_log:
            conn = sqlite3.connect('gestion_netflix.db')
            c = conn.cursor()
            query = "SELECT * FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?"
            c.execute(query, (u_log, p_log))
            result = c.fetchone()
            
            if result:
                email_acc, pass_app = result[2], result[3]
                s_session, p_bot, r_steps = result[8], result[9], result[10]
                
                c.execute("SELECT estado, fecha_vencimiento FROM vendedores WHERE id=?", (result[6],))
                v_status = c.fetchone()
                conn.close()
                
                v_vence_dt = datetime.strptime(v_status[1], '%Y-%m-%d').date()
                if v_status[0] == 0 or v_vence_dt < datetime.now().date():
                    st.error("Servicio temporalmente inactivo.")
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
                            else:
                                st.warning(codigo)
            else:
                st.error("Usuario o clave incorrectos.")
        else:
            st.warning("Por favor rellena todos los campos.")

st.sidebar.markdown("---")
st.sidebar.caption("Sistema v2.0 - 2026")
