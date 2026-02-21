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

# --- 1. CONFIGURACIÓN DE BASE DE DATOS (V4) ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix_v4.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  usuario TEXT UNIQUE, clave TEXT, estado INTEGER, fecha_vencimiento DATE)''')
    
    # Fuentes: Correos Madre (Con filtros de seguridad)
    c.execute('''CREATE TABLE IF NOT EXISTS correos_madre (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,
                 correo_imap TEXT, password_app TEXT, servidor_imap TEXT DEFAULT 'imap.gmail.com',
                 filtro_login INTEGER DEFAULT 1, filtro_temporal INTEGER DEFAULT 1,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    # Fuentes: Bots de Telegram
    c.execute('''CREATE TABLE IF NOT EXISTS bots_telegram (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,
                 bot_username TEXT, string_session TEXT, recipe_steps TEXT,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    # CRM: Clientes finales
    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, plataforma TEXT, email TEXT, 
                  usuario_cliente TEXT UNIQUE, pass_cliente TEXT, vendedor_id INTEGER,
                  estado_pago INTEGER DEFAULT 1, id_madre INTEGER, id_bot INTEGER,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id),
                  FOREIGN KEY(id_madre) REFERENCES correos_madre(id),
                  FOREIGN KEY(id_bot) REFERENCES bots_telegram(id))''')
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

# --- LÓGICA DE EXTRACCIÓN: CORREOS (IMAP) CON FILTROS ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")' if plataforma == "Prime Video" else f'(FROM "info@account.netflix.com" TO "{email_cliente_final}")'
        status, mensajes = mail.search(None, criterio)
        if not mensajes[0]: return "Correo no hallado."
        
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

        # --- APLICACIÓN DE FILTROS DEL VENDEDOR ---
        es_login = "inicio de sesión" in cuerpo.lower() or "nuevo dispositivo" in cuerpo.lower()
        es_temporal = "temporal" in cuerpo.lower() or "viaje" in cuerpo.lower() or "travel" in cuerpo.lower()

        if es_login and not filtro_login:
            return "BLOQUEADO: El vendedor desactivó la entrega automática para Inicios de Sesión."
        if es_temporal and not filtro_temporal:
            return "BLOQUEADO: El vendedor desactivó la entrega automática para Accesos Temporales."

        # --- EXTRACCIÓN ---
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
        return f"Error de conexión IMAP: {str(e)}"

# --- INTERFAZ DE USUARIO ---
st.set_page_config(page_title="Gestión de Cuentas v4.0", layout="centered")
menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Navegación", menu)

if opcion == "Administrador":
    st.header("🔑 Panel de Control Maestro")
    if st.text_input("Clave Maestra", type="password") == "merida2026":
        col_crear, col_lista = st.columns([1, 2])
        
        with col_crear:
            st.subheader("➕ Registrar Vendedor")
            nv = st.text_input("Usuario")
            cv = st.text_input("Contraseña")
            if st.button("Guardar Vendedor"):
                conn = sqlite3.connect('gestion_netflix_v4.db')
                try:
                    venc = (datetime.now() + timedelta(days=30)).date()
                    conn.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", (nv, cv, 1, venc))
                    conn.commit()
                    st.success("Vendedor guardado.")
                except: st.error("Usuario ya existe.")
                conn.close()

        with col_lista:
            st.subheader("👥 Vendedores")
            conn = sqlite3.connect('gestion_netflix_v4.db')
            vendedores = conn.execute("SELECT * FROM vendedores").fetchall()
            for v in vendedores:
                c1, c2, c3 = st.columns([2, 2, 1])
                c1.write(f"**{v[1]}** (Pass: `{v[2]}`)")
                c2.write("🟢 Activo" if v[3] else "🔴 Inactivo")
                if c3.button("Estado", key=f"v_{v[0]}"):
                    conn.execute("UPDATE vendedores SET estado=? WHERE id=?", (0 if v[3] else 1, v[0]))
                    conn.commit()
                    st.rerun()
            conn.close()

elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Portal de Vendedores")
    u_v, p_v = st.text_input("Usuario"), st.text_input("Clave", type="password")
    
    if u_v and p_v:
        conn = sqlite3.connect('gestion_netflix_v4.db')
        c = conn.cursor()
        vend = c.execute("SELECT id, estado FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()
        
        if vend and vend[1] == 1:
            v_id = vend[0]
            
            # --- SEPARACIÓN EN PESTAÑAS (FUENTES VS CLIENTES) ---
            tab_fuentes, tab_clientes = st.tabs(["⚙️ Fuentes de Extracción", "👥 Gestión de Clientes"])
            
            with tab_fuentes:
                st.subheader("1. Mis Correos (Gmail / Dominios Privados)")
                with st.form("f_madre"):
                    tipo_correo = st.radio("Tipo de proveedor:", ["Gmail / Google Workspace", "Webmail (Dominio Privado / cPanel)", "Outlook / Hotmail"])
                    
                    me = st.text_input("Correo Electrónico")
                    mp = st.text_input("Contraseña (o Clave de Aplicación)", type="password")
                    
                    # Campo dinámico para el servidor IMAP
                    servidor_personalizado = "imap.gmail.com"
                    if tipo_correo == "Webmail (Dominio Privado / cPanel)":
                        st.info("💡 Para dominios privados, el servidor suele ser 'mail.tudominio.com'")
                        servidor_personalizado = st.text_input("Servidor IMAP", value="mail.tudominio.com")
                    elif tipo_correo == "Outlook / Hotmail":
                        servidor_personalizado = "outlook.office365.com"

                    st.write("**Filtros de Seguridad:**")
                    f_log = st.checkbox("Permitir entregar códigos de Nuevo Inicio de Sesión", value=True)
                    f_tmp = st.checkbox("Permitir entregar códigos de Acceso Temporal / Viaje", value=True)
                    
                    if st.form_submit_button("Guardar Correo Madre"):
                        c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal) VALUES (?,?,?,?,?,?)", 
                                  (v_id, me, mp, servidor_personalizado, int(f_log), int(f_tmp)))
                        conn.commit()
                        st.success("Correo Madre guardado.")
                
                st.subheader("2. Mis Bots de Telegram")
                with st.form("f_bot"):
                    b_user = st.text_input("Username del Bot (@ejemplo_bot)")
                    s_sess = st.text_area("String Session (Llave)")
                    r_steps = st.text_area("Receta de Pasos")
                    if st.form_submit_button("Guardar Bot"):
                        c.execute("INSERT INTO bots_telegram (vendedor_id, bot_username, string_session, recipe_steps) VALUES (?,?,?,?)", 
                                  (v_id, b_user, s_sess, r_steps))
                        conn.commit()
                        st.success("Bot guardado.")

            with tab_clientes:
                st.subheader("➕ Añadir Nuevo Cliente")
                with st.form("f_cliente"):
                    u_cli = st.text_input("Correo de Streaming (Ej: netflix@cliente.com)")
                    plat = st.selectbox("Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
                    
                    # El vendedor le crea un acceso a su cliente
                    col1, col2 = st.columns(2)
                    c_user = col1.text_input("Usuario para el panel web")
                    c_pass = col2.text_input("Clave para el panel web")
                    
                    # Asignar la fuente de extracción
                    madres = c.execute("SELECT id, correo_imap FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()
                    bots = c.execute("SELECT id, bot_username FROM bots_telegram WHERE vendedor_id=?", (v_id,)).fetchall()
                    
                    opciones_fuente = {"Ninguna": (None, None)}
                    for m in madres: opciones_fuente[f"📧 Correo: {m[1]}"] = (m[0], "madre")
                    for b in bots: opciones_fuente[f"🤖 Bot: {b[1]}"] = (b[0], "bot")
                    
                    sel_fuente = st.selectbox("¿De dónde sacará el código este cliente?", options=list(opciones_fuente.keys()))
                    
                    if st.form_submit_button("Registrar Cliente"):
                        id_ref, tipo = opciones_fuente[sel_fuente]
                        id_m = id_ref if tipo == "madre" else None
                        id_b = id_ref if tipo == "bot" else None
                        
                        try:
                            c.execute("""INSERT INTO cuentas (plataforma, email, usuario_cliente, pass_cliente, vendedor_id, id_madre, id_bot) 
                                         VALUES (?,?,?,?,?,?,?)""", (plat, u_cli, c_user, c_pass, v_id, id_m, id_b))
                            conn.commit()
                            st.success("Cliente registrado con éxito.")
                        except: st.error("Ese usuario web ya existe.")
                
                st.markdown("---")
                st.subheader("📋 Mis Clientes (Control de Pagos)")
                clientes = c.execute("SELECT id, usuario_cliente, email, plataforma, estado_pago FROM cuentas WHERE vendedor_id=?", (v_id,)).fetchall()
                for cli in clientes:
                    cc1, cc2, cc3 = st.columns([2, 2, 1])
                    cc1.write(f"👤 **{cli[1]}** | 📺 {cli[3]}")
                    cc2.write(f"📧 `{cli[2]}`")
                    btn_pago = "🟢 Al día" if cli[4] else "🔴 Pago Vencido"
                    if cc3.button(btn_pago, key=f"pago_{cli[0]}"):
                        c.execute("UPDATE cuentas SET estado_pago=? WHERE id=?", (0 if cli[4] else 1, cli[0]))
                        conn.commit()
                        st.rerun()

        else:
            if vend and vend[1] == 0: st.error("Cuenta de vendedor desactivada.")
            elif u_v: st.error("Credenciales incorrectas.")
        conn.close()

elif opcion == "Panel Cliente":
    st.header("📺 Obtener mi Código")
    u_l, p_l = st.text_input("Mi Usuario"), st.text_input("Mi Clave", type="password")
    
    if st.button("Buscar Código"):
        conn = sqlite3.connect('gestion_netflix_v4.db')
        c = conn.cursor()
        res = c.execute("SELECT * FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?", (u_l, p_l)).fetchone()
        
        if res:
            if res[6] == 0: # Control de pagos
                st.error("🚫 Tu suscripción está inactiva. Contacta a tu vendedor para renovar el pago.")
            else:
                # --- VISTA PREVIA ---
                st.info(f"🔎 Buscando código para la cuenta: **{res[2]}** ({res[1]})")
                
                with st.spinner('Conectando con el servidor...'):
                    codigo = None
                    if res[8]: # Método Bot Telegram
                        bot = c.execute("SELECT string_session, bot_username, recipe_steps FROM bots_telegram WHERE id=?", (res[8],)).fetchone()
                        codigo = asyncio.run(ejecutar_receta_bot(bot[0], bot[1], bot[2], res[2]))
                    
                    elif res[7]: # Método Correo Madre
                        madre = c.execute("SELECT correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal FROM correos_madre WHERE id=?", (res[7],)).fetchone()
                        codigo = obtener_codigo_centralizado(madre[0], madre[1], res[2], res[1], madre[2], madre[3], madre[4])
                    
                    else:
                        st.warning("Tu cuenta no tiene una fuente asignada. Contacta al vendedor.")
                    
                    if codigo:
                        st.markdown("---")
                        if "BLOQUEADO" in str(codigo):
                            st.error(codigo)
                        elif str(codigo).isdigit():
                            st.balloons()
                            st.success("✅ ¡Código extraído con éxito!")
                            st.markdown(f"<div style='text-align: center; border: 2px dashed #4CAF50; padding: 20px; border-radius: 10px;'><h1 style='color: #E50914; margin:0;'>{codigo}</h1></div>", unsafe_allow_html=True)
                        else:
                            st.warning(f"Respuesta del sistema: {codigo}")
        else:
            st.error("Usuario o clave incorrectos.")
        conn.close()
