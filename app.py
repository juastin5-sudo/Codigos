import streamlit as st
import psycopg2
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
DB_URL = "postgresql://neondb_owner:npg_HtF1S5TOhcpd@ep-square-truth-aiq0354u-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require" # <-- ENLACE DE NEON.TECH

# --- 1. CONFIGURACIÓN DE BASE DE DATOS EN LA NUBE ---
def inicializar_db():
    conn = psycopg2.connect(DB_URL)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id SERIAL PRIMARY KEY, 
                 usuario TEXT UNIQUE, clave TEXT, estado INTEGER, fecha_vencimiento DATE)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS correos_madre (
                 id SERIAL PRIMARY KEY, vendedor_id INTEGER,
                 correo_imap TEXT, password_app TEXT, servidor_imap TEXT DEFAULT 'imap.gmail.com',
                 filtro_login INTEGER DEFAULT 1, filtro_temporal INTEGER DEFAULT 1,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS bots_telegram (
                 id SERIAL PRIMARY KEY, vendedor_id INTEGER,
                 bot_username TEXT, plataforma TEXT, string_session TEXT, recipe_steps TEXT,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id SERIAL PRIMARY KEY, usuario_cliente TEXT UNIQUE, 
                 pass_cliente TEXT, vendedor_id INTEGER, estado_pago INTEGER DEFAULT 1,
                 FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    conn.close()

try:
    inicializar_db()
except Exception as e:
    st.error(f"Error conectando a la base de datos: {e}")

# --- LÓGICA DE EXTRACCIÓN: BOT DE TELEGRAM MEJORADA ---
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            if not receta_text or receta_text.strip() == "":
                await client.send_message(bot_username, email_cliente)
                await asyncio.sleep(4)
            else:
                pasos = receta_text.strip().split('\n')
                for paso in pasos:
                    paso = paso.strip()
                    if not paso: continue 
                    if paso.upper() == "[CORREO]":
                        await client.send_message(bot_username, email_cliente)
                    else:
                        await client.send_message(bot_username, paso)
                    await asyncio.sleep(3) 
            
            ultimos_msgs = await client.get_messages(bot_username, limit=1)
            respuesta = ultimos_msgs[0].text if ultimos_msgs else "Sin respuesta del bot."
            return respuesta
    except Exception as e:
        return f"Error con Bot: {str(e)}"

# --- LÓGICA DE EXTRACCIÓN: CORREOS (IMAP) CON FILTROS ESTRICTOS (HTML) ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")' if plataforma == "Prime Video" else f'(FROM "info@account.netflix.com" TO "{email_cliente_final}")'
        status, mensajes = mail.search(None, criterio)
        
        if not mensajes[0]: return None 
        
        ids_mensajes = mensajes[0].split()
        
        # Revisar los últimos 3 mensajes hacia atrás para saltar correos peligrosos y buscar el código real
        for idx in reversed(ids_mensajes[-3:]):
            res, datos = mail.fetch(idx, '(RFC822)')
            msg = email.message_from_bytes(datos[0][1])
            
            cuerpo_html = ""
            cuerpo_texto = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        cuerpo_html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    elif part.get_content_type() == "text/plain":
                        cuerpo_texto = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                cuerpo = cuerpo_html if cuerpo_html else cuerpo_texto
            else:
                cuerpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

            cuerpo_lower = cuerpo.lower()
            es_login = "inicio de sesión" in cuerpo_lower or "nuevo dispositivo" in cuerpo_lower
            es_temporal = "temporal" in cuerpo_lower or "viaje" in cuerpo_lower or "travel" in cuerpo_lower

            if plataforma == "Prime Video":
                match = re.search(r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})', cuerpo, re.IGNORECASE)
                if match:
                    return match.group(1)
                continue # Si no tiene código, revisa el correo anterior

            elif plataforma == "Netflix":
                # REGLA ESTRICTA: Si NO es inicio de sesión ni temporal (ej. cambio de clave), lo ignoramos
                if not es_login and not es_temporal:
                    continue 
                
                # Si ES un correo válido, revisamos las casillas que marcó el vendedor
                if es_login and not filtro_login:
                    return "BLOQUEADO: El vendedor desactivó la entrega automática para Inicios de Sesión."
                if es_temporal and not filtro_temporal:
                    return "BLOQUEADO: El vendedor desactivó la entrega automática para Accesos Temporales."
                
                return cuerpo # Pasa todas las pruebas de seguridad, se lo entregamos al cliente
            
            else:
                # Para otras plataformas, entregamos el primero que encuentre
                return cuerpo

        return None # Si revisó los últimos 3 y ninguno era un código válido
    except Exception as e:
        return None 

# --- INTERFAZ DE USUARIO ---
st.set_page_config(page_title="Gestión de Cuentas v6.0", layout="centered")
menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Navegación", menu)

if 'admin_logueado' not in st.session_state: st.session_state['admin_logueado'] = False
if 'vendedor_logueado' not in st.session_state: st.session_state['vendedor_logueado'] = False
if 'id_vend_actual' not in st.session_state: st.session_state['id_vend_actual'] = None
if 'nombre_vend_actual' not in st.session_state: st.session_state['nombre_vend_actual'] = ""

# ==========================================
# PANEL ADMINISTRADOR
# ==========================================
if opcion == "Administrador":
    st.header("🔑 Panel de Control Maestro")
    
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
                    conn = psycopg2.connect(DB_URL)
                    c = conn.cursor()
                    try:
                        venc = (datetime.now() + timedelta(days=30)).date()
                        c.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (%s,%s,%s,%s)", (nv, cv, 1, venc))
                        conn.commit()
                        st.success("Vendedor guardado.")
                    except: st.error("Usuario ya existe.")
                    conn.close()
                else:
                    st.warning("Llena los campos.")

        with col_lista:
            st.subheader("👥 Vendedores")
            conn = psycopg2.connect(DB_URL)
            c = conn.cursor()
            c.execute("SELECT * FROM vendedores")
            vendedores = c.fetchall()
            for v in vendedores:
                c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1])
                c1.write(f"**{v[1]}** (Pass: `{v[2]}`)")
                c2.write("🟢 Activo" if v[3] else "🔴 Inactivo")
                if c3.button("Estado", key=f"v_stat_{v[0]}"):
                    c.execute("UPDATE vendedores SET estado=%s WHERE id=%s", (0 if v[3] else 1, v[0]))
                    conn.commit()
                    st.rerun()
                if c4.button("🗑️", key=f"v_del_{v[0]}"):
                    c.execute("DELETE FROM correos_madre WHERE vendedor_id=%s", (v[0],))
                    c.execute("DELETE FROM bots_telegram WHERE vendedor_id=%s", (v[0],))
                    c.execute("DELETE FROM cuentas WHERE vendedor_id=%s", (v[0],))
                    c.execute("DELETE FROM vendedores WHERE id=%s", (v[0],))
                    conn.commit()
                    st.rerun()
            conn.close()

# ==========================================
# PANEL VENDEDOR
# ==========================================
elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Portal de Vendedores")
    
    if not st.session_state['vendedor_logueado']:
        with st.form("form_login_vendedor"):
            u_v = st.text_input("Usuario")
            p_v = st.text_input("Clave", type="password")
            btn_ingresar_vend = st.form_submit_button("Iniciar Sesión")
            
            if btn_ingresar_vend:
                if u_v and p_v:
                    conn = psycopg2.connect(DB_URL)
                    c = conn.cursor()
                    c.execute("SELECT id, estado, usuario FROM vendedores WHERE usuario=%s AND clave=%s", (u_v, p_v))
                    vend = c.fetchone()
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
    else:
        st.success(f"Bienvenido, {st.session_state['nombre_vend_actual']}")
        if st.button("🚪 Cerrar Sesión"):
            st.session_state['vendedor_logueado'] = False
            st.session_state['id_vend_actual'] = None
            st.rerun()
            
        st.markdown("---")
        v_id = st.session_state['id_vend_actual']
        conn = psycopg2.connect(DB_URL)
        c = conn.cursor()
            
        tab_fuentes, tab_clientes = st.tabs(["⚙️ Fuentes de Extracción", "👥 Gestión de Clientes"])
        
        with tab_fuentes:
            st.info("Puedes registrar todos los correos y bots que necesites. El sistema buscará en todos ellos automáticamente.")
            st.subheader("📧 Mis Correos (Gmail / Dominios Privados)")
            tipo_correo = st.radio("Tipo de proveedor:", ["Gmail / Google Workspace", "Webmail (Dominio Privado / cPanel)", "Outlook / Hotmail"])
            
            with st.form("f_madre"):
                me = st.text_input("Correo Electrónico")
                mp = st.text_input("Contraseña (o Clave de Aplicación)", type="password")
                
                servidor_personalizado = "imap.gmail.com"
                if tipo_correo == "Webmail (Dominio Privado / cPanel)":
                    servidor_personalizado = st.text_input("Servidor IMAP (Ej: mail.tudominio.com)", value="mail.tudominio.com")
                elif tipo_correo == "Outlook / Hotmail":
                    servidor_personalizado = "outlook.office365.com"
                    st.caption("Servidor configurado automáticamente para Outlook/Hotmail.")
                else:
                    st.caption("Servidor configurado automáticamente para Gmail.")

                st.write("**Filtros de Seguridad:**")
                col_f1, col_f2 = st.columns(2)
                f_log = col_f1.checkbox("Permitir Nuevo Inicio de Sesión", value=True)
                f_tmp = col_f2.checkbox("Permitir Acceso Temporal", value=True)
                
                if st.form_submit_button("Añadir Correo"):
                    c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal) VALUES (%s,%s,%s,%s,%s,%s)", 
                              (v_id, me, mp, servidor_personalizado, int(f_log), int(f_tmp)))
                    conn.commit()
                    st.success("Correo añadido.")
                    st.rerun()
            
            c.execute("SELECT id, correo_imap, servidor_imap FROM correos_madre WHERE vendedor_id=%s", (v_id,))
            correos_guardados = c.fetchall()
            if correos_guardados:
                st.write("**Tus correos activos:**")
                for cg in correos_guardados:
                    cc1, cc2 = st.columns([5, 1])
                    cc1.caption(f"✅ {cg[1]} ({cg[2]})")
                    if cc2.button("🗑️", key=f"del_cm_{cg[0]}"):
                        c.execute("DELETE FROM correos_madre WHERE id=%s", (cg[0],))
                        conn.commit()
                        st.rerun()

            st.markdown("---")
            st.subheader("🤖 Mis Bots de Telegram")
            with st.form("f_bot"):
                b_user = st.text_input("Username del Bot (@ejemplo_bot)")
                plat_bot = st.selectbox("¿Para qué plataforma es este bot?", ["Todas las plataformas", "Netflix", "Prime Video", "Disney+", "Otros"])
                s_sess = st.text_area("String Session (Llave)")
                r_steps = st.text_area("Receta de Pasos (Opcional)")
                if st.form_submit_button("Añadir Bot"):
                    c.execute("INSERT INTO bots_telegram (vendedor_id, bot_username, plataforma, string_session, recipe_steps) VALUES (%s,%s,%s,%s,%s)", 
                              (v_id, b_user, plat_bot, s_sess, r_steps))
                    conn.commit()
                    st.success("Bot añadido.")
                    st.rerun()
            
            c.execute("SELECT id, bot_username, plataforma FROM bots_telegram WHERE vendedor_id=%s", (v_id,))
            bots_guardados = c.fetchall()
            if bots_guardados:
                st.write("**Tus bots activos:**")
                for bg in bots_guardados:
                    bc1, bc2 = st.columns([5, 1])
                    bc1.caption(f"✅ {bg[1]} ({bg[2]})")
                    if bc2.button("🗑️", key=f"del_bot_{bg[0]}"):
                        c.execute("DELETE FROM bots_telegram WHERE id=%s", (bg[0],))
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
                        c.execute("INSERT INTO cuentas (usuario_cliente, pass_cliente, vendedor_id) VALUES (%s,%s,%s)", (c_user, c_pass, v_id))
                        conn.commit()
                        st.success("Acceso creado. Entrégale estos datos a tu cliente.")
                        st.rerun()
                    except: st.error("Ese usuario web ya existe. Intenta con otro.")
            
            st.markdown("---")
            st.subheader("📋 Control de Pagos de Clientes")
            c.execute("SELECT id, usuario_cliente, estado_pago, pass_cliente FROM cuentas WHERE vendedor_id=%s", (v_id,))
            clientes = c.fetchall()
            for cli in clientes:
                cc1, cc2, cc3 = st.columns([3, 1.5, 0.5])
                cc1.write(f"👤 **{cli[1]}** | 🔑 Clave: `{cli[3]}`")
                btn_pago = "🟢 Suscripción Activa" if cli[2] else "🔴 Pago Vencido"
                if cc2.button(btn_pago, key=f"pago_{cli[0]}"):
                    c.execute("UPDATE cuentas SET estado_pago=%s WHERE id=%s", (0 if cli[2] else 1, cli[0]))
                    conn.commit()
                    st.rerun()
                if cc3.button("🗑️", key=f"del_cli_{cli[0]}"):
                    c.execute("DELETE FROM cuentas WHERE id=%s", (cli[0],))
                    conn.commit()
                    st.rerun()
        conn.close()

# ==========================================
# PANEL CLIENTE
# ==========================================
elif opcion == "Panel Cliente":
    st.header("📺 Buscador de Códigos")
    
    if 'cliente_logueado' not in st.session_state: st.session_state['cliente_logueado'] = False

    if not st.session_state['cliente_logueado']:
        st.write("Inicia sesión con los datos que te dio tu vendedor:")
        with st.form("login_cliente"):
            u_l = st.text_input("Mi Usuario")
            p_l = st.text_input("Mi Clave", type="password")
            btn_entrar_cli = st.form_submit_button("Entrar")
            
            if btn_entrar_cli:
                conn = psycopg2.connect(DB_URL)
                c = conn.cursor()
                c.execute("SELECT id, vendedor_id, estado_pago, usuario_cliente FROM cuentas WHERE usuario_cliente=%s AND pass_cliente=%s", (u_l, p_l))
                res = c.fetchone()
                conn.close()
                
                if res:
                    if res[2] == 0:
                        st.error("🚫 Tu suscripción está inactiva. Contacta a tu vendedor para renovar.")
                    else:
                        st.session_state['cliente_logueado'] = True
                        st.session_state['vendedor_id'] = res[1]
                        st.session_state['nombre_cli'] = res[3]
                        st.rerun()
                else:
                    st.error("Usuario o clave incorrectos.")
    
    else:
        st.success(f"Hola, {st.session_state['nombre_cli']}.")
        if st.button("Cerrar Sesión"):
            st.session_state['cliente_logueado'] = False
            st.rerun()

        st.markdown("---")
        st.subheader("🔍 Buscar mi código")
        plat = st.selectbox("Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
        correo_buscar = st.text_input("Ingresa el correo de tu cuenta de streaming:")
        
        if st.button("Extraer Código"):
            if correo_buscar:
                st.info(f"Escaneando servidores en busca de correos para: **{correo_buscar}**")
                
                conn = psycopg2.connect(DB_URL)
                c = conn.cursor()
                v_id = st.session_state['vendedor_id']
                c.execute("SELECT correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal FROM correos_madre WHERE vendedor_id=%s", (v_id,))
                correos_vendedor = c.fetchall()
                c.execute("SELECT bot_username, string_session, recipe_steps, plataforma FROM bots_telegram WHERE vendedor_id=%s", (v_id,))
                bots_vendedor = c.fetchall()
                conn.close()

                codigo_encontrado = None
                
                with st.spinner('Revisando buzones y preguntando a los bots...'):
                    for madre in correos_vendedor:
                        if not codigo_encontrado:
                            resultado = obtener_codigo_centralizado(madre[0], madre[1], correo_buscar, plat, madre[2], madre[3], madre[4])
                            if resultado:
                                codigo_encontrado = resultado
                    
                    if not codigo_encontrado:
                        for bot in bots_vendedor:
                            if not codigo_encontrado:
                                bot_plat = bot[3]
                                if bot_plat == "Todas las plataformas" or bot_plat == plat:
                                    resultado = asyncio.run(ejecutar_receta_bot(bot[1], bot[0], bot[2], correo_buscar))
                                    if "Sin respuesta" not in resultado and "Error" not in resultado:
                                        codigo_encontrado = resultado

                if codigo_encontrado:
                    st.markdown("---")
                    if "BLOQUEADO" in str(codigo_encontrado):
                        st.error(codigo_encontrado)
                    elif str(codigo_encontrado).isdigit() or len(str(codigo_encontrado)) < 20: 
                        st.balloons()
                        st.success("✅ ¡Código extraído con éxito!")
                        st.markdown(f"<div style='text-align: center; border: 2px dashed #4CAF50; padding: 20px; border-radius: 10px;'><h1 style='color: #E50914; margin:0;'>{codigo_encontrado}</h1></div>", unsafe_allow_html=True)
                    else:
                        st.success("✅ ¡Correo encontrado!")
                        html_modificado = f'<base target="_blank">{codigo_encontrado}'
                        st.components.v1.html(html_modificado, height=600, scrolling=True)
                else:
                    st.error("No se encontró ningún correo reciente. Intenta de nuevo en unos minutos.")
            else:
                st.warning("Por favor, ingresa el correo de streaming.")
