import streamlit as st
import psycopg2
import pandas as pd
import imaplib
import email
import re
import requests
import asyncio
import html
import time
import threading
from email.header import decode_header
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from telethon import TelegramClient
from telethon.sessions import StringSession

# --- CONSTANTES ---
MI_API_ID = 34062718
MI_API_HASH = 'ca9d5cbc6ce832c6660f949a5567a159'
try:
    DB_URL = str(st.secrets["NEON_URL"]).strip()
except Exception:
    DB_URL = ""
    st.error("Falta la dirección de la base de datos. En Streamlit > Settings > Secrets agrega: NEON_URL = \"...\"")
    st.stop()

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
                 correos_permitidos TEXT DEFAULT '',
                 FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    
    try:
        c.execute("ALTER TABLE cuentas ADD COLUMN correos_permitidos TEXT DEFAULT ''")
        conn.commit()
    except psycopg2.errors.DuplicateColumn:
        conn.rollback() 
        
    conn.close()

try:
    inicializar_db()
except Exception as e:
    st.error(f"Error conectando a la base de datos: {e}")

# --- OBTENER ID DEL ÚLTIMO MENSAJE ---
async def obtener_ultimo_id(session_str, bot_username):
    try:
        async with TelegramClient(StringSession(session_str.strip()), MI_API_ID, MI_API_HASH) as client:
            last = await client.get_messages(bot_username, limit=1)
            return last[0].id if last else 0
    except:
        return 0

# --- TURNOS PARA TELEGRAM: solo un cliente a la vez usa tu cuenta de Telegram ---
@st.cache_resource
def candado_telegram():
    return threading.Lock()

AVISO_OCUPADO = [{"id": 0, "role": "assistant", "content": "Hay muchos clientes pidiendo código en este momento. Intenta de nuevo en un minuto.", "botones": []}]

def correr_con_turno(coro_fn, *args, defecto=None, espera_max=90, duracion_max=75):
    """Ejecuta una acción de Telegram respetando el turno. Si hay otro cliente usando Telegram, espera a que termine."""
    cand = candado_telegram()
    if not cand.acquire(blocking=False):
        aviso = st.empty()
        aviso.info("⏳ Hay otro cliente pidiendo su código. Tu turno empieza en unos segundos, no cierres la página...")
        obtuvo = cand.acquire(timeout=espera_max)
        aviso.empty()
        if not obtuvo:
            return defecto
    try:
        return asyncio.run(asyncio.wait_for(coro_fn(*args), timeout=duracion_max))
    except Exception:
        return defecto
    finally:
        cand.release()

# --- RECETA AUTOMÁTICA DEL BOT (para que el cliente no tenga que seguir los pasos) ---
async def ejecutar_receta(session_str, bot_username, pasos, correo):
    """Hace por el cliente los pasos del bot. Cada paso es una línea:
       /start            -> envía ese texto al bot
       [BOTON]Texto      -> pulsa el botón que contenga ese texto
       {correo}          -> se cambia por el correo que escribió el cliente
    Devuelve la conversación (igual que el chat manual)."""
    try:
        async with TelegramClient(StringSession(session_str.strip()), MI_API_ID, MI_API_HASH) as client:
            previos = await client.get_messages(bot_username, limit=1)
            min_id = previos[0].id if previos else 0
            for paso in pasos:
                paso = paso.strip().replace("{correo}", correo)
                if not paso:
                    continue
                if paso.upper().startswith("[ESPERA]"):
                    try:
                        await asyncio.sleep(min(int(paso[len("[ESPERA]"):].strip()), 30))
                    except ValueError:
                        pass
                    continue
                if paso.startswith("[BOTON]"):
                    objetivo = paso[len("[BOTON]"):].strip().lower()
                    pulsado = False
                    for m in await client.get_messages(bot_username, limit=5):
                        for fila in (m.buttons or []):
                            for b in fila:
                                if objetivo in (b.text or "").lower():
                                    await b.click()
                                    pulsado = True
                                    break
                            if pulsado:
                                break
                        if pulsado:
                            break
                else:
                    await client.send_message(bot_username, paso)
                await asyncio.sleep(3)
            await asyncio.sleep(3)  # tiempo extra para la respuesta final del bot
            for _ in range(4):      # si la respuesta todavía no trae un código, espera un poco más (máx. 12 s)
                recientes = await client.get_messages(bot_username, limit=3, min_id=min_id)
                entrantes = [m for m in recientes if not m.out]
                if entrantes and re.search(r'\b\d{4,8}\b', entrantes[0].text or ""):
                    break
                await asyncio.sleep(3)
            mensajes = await client.get_messages(bot_username, limit=20, min_id=min_id)
            historial = []
            for m in reversed(mensajes):
                texto = m.text if m.text else ""
                botones = []
                if m.reply_markup and hasattr(m.reply_markup, 'rows'):
                    for row in m.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, 'text'):
                                botones.append(btn.text)
                if texto or botones:
                    historial.append({
                        "id": m.id,
                        "role": "user" if m.out else "assistant",
                        "content": texto if texto else "🤖 [Menú interactivo]",
                        "botones": botones if not m.out else []
                    })
            return historial
    except Exception as e:
        return [{"id": 0, "role": "assistant", "content": f"Error de conexión con Telegram: {str(e)}", "botones": []}]

# --- LÓGICA DE EXTRACCIÓN: BOT DE TELEGRAM ---
async def enviar_y_recibir_bot(session_str, bot_username, mensaje, min_id=0):
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            if mensaje:
                if mensaje.startswith("[BOTON]"):
                    btn_text = mensaje.replace("[BOTON]", "")
                    mensajes_recientes = await client.get_messages(bot_username, limit=5)
                    for m in mensajes_recientes:
                        if m.reply_markup:
                            try:
                                await m.click(text=btn_text) 
                                break
                            except Exception as e:
                                pass
                else:
                    await client.send_message(bot_username, mensaje)
                await asyncio.sleep(3) 
            
            mensajes = await client.get_messages(bot_username, limit=20, min_id=min_id)
            historial = []
            for m in reversed(mensajes):
                texto = m.text if m.text else ""
                botones = []
                if m.reply_markup and hasattr(m.reply_markup, 'rows'):
                    for row in m.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, 'text'):
                                botones.append(btn.text)
                
                if texto or botones: 
                    historial.append({
                        "role": "user" if m.out else "assistant", 
                        "content": texto if texto else "🤖 [Menú interactivo]",
                        "botones": botones if not m.out else []
                    })
            return historial
    except Exception as e:
        return [{"role": "assistant", "content": f"Error de conexión con Telegram: {str(e)}", "botones": []}]

async def iniciar_grabacion(session_str, bot_username):
    """Una sola conexión: anota el último mensaje, envía /start y devuelve (id_inicial, conversación)."""
    try:
        async with TelegramClient(StringSession(session_str.strip()), MI_API_ID, MI_API_HASH) as client:
            last = await client.get_messages(bot_username, limit=1)
            min_id = last[0].id if last else 0
            await client.send_message(bot_username, "/start")
            await asyncio.sleep(3)
            mensajes = await client.get_messages(bot_username, limit=20, min_id=min_id)
            historial = []
            for m in reversed(mensajes):
                botones = []
                if m.reply_markup and hasattr(m.reply_markup, 'rows'):
                    for row in m.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, 'text'):
                                botones.append(btn.text)
                if m.text or botones:
                    historial.append({"role": "user" if m.out else "assistant",
                                      "content": m.text if m.text else "🤖 [Menú interactivo]",
                                      "botones": botones if not m.out else []})
            return min_id, historial
    except Exception as e:
        return None, f"Error de conexión con Telegram: {e}"

# --- CÓDIGOS POR CORREO PARA DISNEY+ Y PRIME VIDEO ---
MINUTOS_VALIDOS = 30  # un código más viejo que esto ya venció, no se entrega

def _texto_limpio(msg):
    """(asunto, texto del cuerpo sin etiquetas HTML)."""
    asunto = ""
    if msg.get("Subject"):
        partes = decode_header(msg.get("Subject"))
        for p, enc in partes:
            asunto += p.decode(enc or 'utf-8', errors='ignore') if isinstance(p, bytes) else str(p)
    cuerpo = ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() in ("text/plain", "text/html"):
            try:
                cuerpo += " " + part.get_payload(decode=True).decode('utf-8', errors='ignore')
            except Exception:
                pass
    texto = html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(style|script).*?</\1>', ' ', cuerpo)))
    return asunto, re.sub(r'\s+', ' ', texto)

def _es_reciente(msg, minutos=MINUTOS_VALIDOS):
    try:
        f = parsedate_to_datetime(msg.get("Date"))
        if f.tzinfo is None:
            f = f.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - f) <= timedelta(minutes=minutos)
    except Exception:
        return True

def _es_correo_peligroso(asunto):
    """Correos para cambiar la contraseña o el correo de la cuenta: nunca se entregan al cliente."""
    a = asunto.lower()
    es_codigo_unico = any(k in a for k in ("un solo uso", "one-time", "one time", "único", "unico", "acceso", "sign in", "log in", "login"))
    if any(k in a for k in ("restablec", "reset", "cambiar el correo", "change your email", "cambio de correo", "actualiz", "update your")):
        return True
    if ("contraseña" in a or "password" in a) and not es_codigo_unico:
        return True
    return False

def _extraer_codigo_6(texto):
    m = re.search(r'(?:c[oó]digo|code|otp|passcode|contrase[nñ]a de un solo uso)[^0-9]{0,80}(\d{6})\b', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{6})\b', texto)
    return m.group(1) if m else None

def buscar_codigo_simple(mail, email_cliente, plataforma):
    """Busca el código más reciente (menos de 30 minutos) enviado a ese correo por Amazon / Prime Video."""
    remitentes = ["amazon", "primevideo"]
    ids = set()
    for rem in remitentes:
        status, mensajes = mail.search(None, f'(FROM "{rem}" TO "{email_cliente}")')
        if mensajes and mensajes[0]:
            ids.update(int(i) for i in mensajes[0].split())
    for i in sorted(ids, reverse=True)[:15]:
        res, datos = mail.fetch(str(i), '(RFC822)')
        msg = email.message_from_bytes(datos[0][1])
        if not _es_reciente(msg):
            continue
        asunto, texto = _texto_limpio(msg)
        if _es_correo_peligroso(asunto):
            continue
        codigo = _extraer_codigo_6(asunto + " " + texto)
        if codigo:
            return codigo
    return None

def diagnostico_correos(email_madre, pass_app_madre, email_cliente, imap_serv, cantidad=10):
    """Últimos correos recibidos para un correo: quién lo envió, asunto, fecha y si se detectó un código."""
    filas = []
    mail = imaplib.IMAP4_SSL(imap_serv)
    mail.login(email_madre, pass_app_madre)
    mail.select("inbox")
    status, mensajes = mail.search(None, f'(TO "{email_cliente}")')
    ids = mensajes[0].split() if mensajes and mensajes[0] else []
    for i in reversed(ids[-cantidad:]):
        res, datos = mail.fetch(i, '(RFC822)')
        msg = email.message_from_bytes(datos[0][1])
        asunto, texto = _texto_limpio(msg)
        filas.append({"De": str(msg.get("From", ""))[:60], "Asunto": asunto[:80], "Fecha": str(msg.get("Date", ""))[:31],
                      "¿Reciente?": "sí" if _es_reciente(msg) else "no", "¿Bloqueado?": "sí" if _es_correo_peligroso(asunto) else "no",
                      "Código detectado": _extraer_codigo_6(asunto + " " + texto) or ""})
    try:
        mail.logout()
    except Exception:
        pass
    return filas

# --- LÓGICA DE EXTRACCIÓN: CORREOS (IMAP) ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal, tipo_solicitud=None):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")

        if plataforma == "Prime Video":
            return buscar_codigo_simple(mail, email_cliente_final, plataforma)
        
        criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")' if plataforma == "Prime Video" else f'(FROM "netflix.com" TO "{email_cliente_final}")'
        status, mensajes = mail.search(None, criterio)
        
        if not mensajes[0]: return None 
        
        ids_mensajes = mensajes[0].split()
        
        for idx in reversed(ids_mensajes[-30:]):
            res, datos = mail.fetch(idx, '(RFC822)')
            msg = email.message_from_bytes(datos[0][1])
            
            asunto_decodificado = ""
            if msg.get("Subject"):
                subj_bytes, encoding = decode_header(msg.get("Subject"))[0]
                if isinstance(subj_bytes, bytes):
                    asunto_decodificado = subj_bytes.decode(encoding if encoding else 'utf-8', errors='ignore')
                else:
                    asunto_decodificado = str(subj_bytes)
            asunto_lower = asunto_decodificado.lower()

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

            if plataforma == "Prime Video":
                match = re.search(r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})', cuerpo, re.IGNORECASE)
                if match: return match.group(1)
                continue

            elif plataforma == "Netflix":
                cuerpo_limpio = html.unescape(re.sub(r'<[^>]+>', '', cuerpo)).lower()
                
                # ESCUDO ANTI-ROBO BLINDADO CON TUS CAPTURAS EXACTAS (AHORA INCLUYE EL ASUNTO DE VERIFICACIÓN)
                es_peligroso = (
                    "cambio de cuenta" in asunto_lower or 
                    "contraseña" in asunto_lower or 
                    "email" in asunto_lower or
                    "código de verificación" in asunto_lower or
                    "confirma el cambio" in cuerpo_limpio or
                    "cambiar la información" in cuerpo_limpio or
                    "código de verificación" in cuerpo_limpio
                )
                
                if es_peligroso:
                    continue

                es_temporal_real = (
                    "acceso temporal" in asunto_lower or 
                    "acceso temporal" in cuerpo_limpio or 
                    "obtener código" in cuerpo_limpio or 
                    "fuera de tu hogar" in cuerpo_limpio or
                    "temporal" in asunto_lower or 
                    "hogar" in asunto_lower or 
                    "viaje" in asunto_lower or 
                    "televisor" in asunto_lower or 
                    "temporal" in cuerpo_limpio or 
                    "hogar" in cuerpo_limpio or 
                    "viaje" in cuerpo_limpio
                )
                
                es_login_real = (
                    "iniciar sesión" in asunto_lower or 
                    "inicio de sesión" in asunto_lower or 
                    "iniciar sesión" in cuerpo_limpio or 
                    "escribe este código" in cuerpo_limpio or 
                    "ingresa este código" in cuerpo_limpio or 
                    "completa tu solicitud" in cuerpo_limpio or 
                    "entrar" in cuerpo_limpio
                )
                
                if es_temporal_real:
                    es_login_real = False

                if tipo_solicitud == "Inicio de Sesión (Nuevo dispositivo)":
                    if not es_login_real: continue 
                    if not filtro_login: return "BLOQUEADO: El vendedor desactivó la entrega automática para Inicios de Sesión."
                    return cuerpo

                elif tipo_solicitud == "Acceso Temporal (Viaje / Hogar)":
                    if not es_temporal_real: continue 
                    if not filtro_temporal: return "BLOQUEADO: El vendedor desactivó la entrega automática para Accesos Temporales."
                    return cuerpo
                
                else:
                    if (es_login_real and filtro_login) or (es_temporal_real and filtro_temporal):
                        return cuerpo
                    continue

            else:
                return cuerpo

        return None 
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

if 'modo_chat' not in st.session_state: st.session_state['modo_chat'] = False
if 'chat_historial' not in st.session_state: st.session_state['chat_historial'] = []
if 'bot_activo' not in st.session_state: st.session_state['bot_activo'] = None
if 'bot_min_id' not in st.session_state: st.session_state['bot_min_id'] = 0

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
            
        tab_fuentes, tab_clientes, tab_diag = st.tabs(["⚙️ Fuentes de Extracción", "👥 Gestión de Clientes", "🔍 Diagnóstico de correos"])
        
        with tab_fuentes:
            st.info("Registra tus correos madre aquí.")
            st.subheader("📧 Mis Correos")
            tipo_correo = st.radio("Tipo de proveedor:", ["Gmail / Google Workspace", "Webmail (Dominio Privado / cPanel)", "Outlook / Hotmail"])
            
            with st.form("f_madre"):
                me = st.text_input("Correo Electrónico")
                mp = st.text_input("Contraseña (o Clave de Aplicación)", type="password")
                
                servidor_personalizado = "imap.gmail.com"
                if tipo_correo == "Webmail (Dominio Privado / cPanel)":
                    servidor_personalizado = st.text_input("Servidor IMAP (Ej: mail.tudominio.com)", value="mail.tudominio.com")
                elif tipo_correo == "Outlook / Hotmail":
                    servidor_personalizado = "outlook.office365.com"
                
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
            for cg in correos_guardados:
                cc1, cc2 = st.columns([5, 1])
                cc1.caption(f"✅ {cg[1]} ({cg[2]})")
                if cc2.button("🗑️", key=f"del_cm_{cg[0]}"):
                    c.execute("DELETE FROM correos_madre WHERE id=%s", (cg[0],))
                    conn.commit()
                    st.rerun()

            st.markdown("---")
            st.subheader("🤖 Mis Bots de Telegram")
            with st.form("f_bot", clear_on_submit=True):
                b_user = st.text_input("Username del Bot (@ejemplo_bot)")
                plat_bot = st.selectbox("¿Para qué plataforma?", ["Todas las plataformas", "Netflix", "Prime Video", "Disney+", "Otros"])
                s_sess = st.text_area("String Session")
                r_steps = st.text_area("Pasos automáticos (opcional, uno por línea)", help="Ejemplo:\n/start\n[BOTON]Disney\n[BOTON]Código\n{correo}\n\n/texto = se lo envía al bot. [BOTON]texto = pulsa ese botón. {correo} = el correo que escribe el cliente. [ESPERA]10 = espera 10 segundos. Solo funciona si eliges la plataforma exacta (no \"Todas\").")
                if st.form_submit_button("Añadir Bot"):
                    c.execute("INSERT INTO bots_telegram (vendedor_id, bot_username, plataforma, string_session, recipe_steps) VALUES (%s,%s,%s,%s,%s)", 
                              (v_id, b_user, plat_bot, s_sess, r_steps))
                    conn.commit()
                    st.success("Bot añadido.")
                    st.rerun()

            c.execute("SELECT id, bot_username, plataforma, recipe_steps FROM bots_telegram WHERE vendedor_id=%s", (v_id,))
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
                    with st.expander(f"✏️ Pasos automáticos de {bg[1]} ({bg[2]})"):
                        pasos_nuevos = st.text_area("Un paso por línea (/texto, [BOTON]texto, {correo})", value=bg[3] or "", key=f"rs_{bg[0]}", height=120)
                        if st.button("💾 Guardar pasos", key=f"rsb_{bg[0]}"):
                            c.execute("UPDATE bots_telegram SET recipe_steps=%s WHERE id=%s", (pasos_nuevos, bg[0]))
                            conn.commit()
                            st.success("Pasos guardados.")

        with tab_clientes:
            st.subheader("👥 Gestión de Clientes")
            with st.form("f_cliente_nuevo"):
                c_user = st.text_input("Usuario web")
                c_pass = st.text_input("Clave web")
                c_correos = st.text_input("Correos de streaming permitidos (Si son varios, sepáralos por coma)")
                if st.form_submit_button("Registrar Cliente"):
                    try:
                        c.execute("INSERT INTO cuentas (usuario_cliente, pass_cliente, vendedor_id, correos_permitidos) VALUES (%s,%s,%s,%s)", (c_user, c_pass, v_id, c_correos.lower().strip()))
                        conn.commit()
                        st.success("Cliente registrado exitosamente.")
                        st.rerun()
                    except: st.error("El usuario ya existe.")
            
            st.markdown("---")
            
            st.write("**Lista de Clientes y Accesos**")
            busqueda_cliente = st.text_input("🔍 Buscar cliente por usuario web:", placeholder="Escribe aquí para filtrar...").strip().lower()
            
            c.execute("SELECT id, usuario_cliente, estado_pago, pass_cliente, correos_permitidos FROM cuentas WHERE vendedor_id=%s", (v_id,))
            todos_los_clientes = c.fetchall()
            
            for cli in todos_los_clientes:
                if busqueda_cliente and busqueda_cliente not in cli[1].lower():
                    continue
                
                with st.expander(f"👤 {cli[1]} | 🔑 Clave: {cli[3]}"):
                    estado_texto = "🟢 Activo" if cli[2] else "🔴 Vencido"
                    st.write(f"**Estado de suscripción:** {estado_texto}")
                    
                    with st.form(key=f"f_edit_{cli[0]}"):
                        correos_actuales = cli[4] if cli[4] else ""
                        nuevos_correos = st.text_input("Correos permitidos para este cliente:", value=correos_actuales)
                        
                        col1, col2, col3 = st.columns(3)
                        if col1.form_submit_button("💾 Guardar Correos"):
                            c.execute("UPDATE cuentas SET correos_permitidos=%s WHERE id=%s", (nuevos_correos.lower().strip(), cli[0]))
                            conn.commit()
                            st.success("¡Correos actualizados!")
                            st.rerun()
                            
                        if col2.form_submit_button("⏸️ Cambiar Estado"):
                            c.execute("UPDATE cuentas SET estado_pago=%s WHERE id=%s", (0 if cli[2] else 1, cli[0]))
                            conn.commit()
                            st.rerun()
                            
                        if col3.form_submit_button("🗑️ Eliminar Cliente"):
                            c.execute("DELETE FROM cuentas WHERE id=%s", (cli[0],))
                            conn.commit()
                            st.rerun()

        with tab_diag:
            st.subheader("🔍 ¿Por qué no me sale el código?")
            st.caption("Muestra los últimos correos que llegaron a una cuenta, quién los envió, el asunto y si la página detectó un código. Sirve para ver cómo llegan los correos de Prime Video (Netflix y Amazon).")
            c.execute("SELECT correo_imap, password_app, servidor_imap FROM correos_madre WHERE vendedor_id=%s", (v_id,))
            madres_d = c.fetchall()
            if not madres_d:
                st.info("Primero registra un correo madre en 'Fuentes de Extracción'.")
            else:
                madre_sel = st.selectbox("Correo madre donde llegan los correos:", [m[0] for m in madres_d], key="diag_madre")
                correo_diag = st.text_input("Correo de la cuenta de streaming (al que llegan los códigos):", key="diag_correo").strip().lower()
                if st.button("Buscar correos", key="diag_btn") and correo_diag:
                    datos_m = next(m for m in madres_d if m[0] == madre_sel)
                    try:
                        with st.spinner("Revisando..."):
                            filas_d = diagnostico_correos(datos_m[0], datos_m[1], correo_diag, datos_m[2])
                        if filas_d:
                            st.dataframe(pd.DataFrame(filas_d), hide_index=True)
                        else:
                            st.warning("No llegó ningún correo para esa dirección a ese correo madre. Revisa que el reenvío o la dirección estén bien.")
                    except Exception as e_d:
                        st.error(f"No pude entrar al correo: {e_d}")

            st.markdown("---")
            st.subheader("🤖 Probar los pasos automáticos de un bot")
            c.execute("SELECT bot_username, string_session, recipe_steps, plataforma, id FROM bots_telegram WHERE vendedor_id=%s", (v_id,))
            bots_t = c.fetchall()
            bots_con_pasos = [b for b in bots_t if str(b[2] or "").strip()]
            if not bots_con_pasos:
                st.info("Ningún bot tiene pasos automáticos todavía. Agrégalos en 'Fuentes de Extracción'.")
            else:
                bot_sel = st.selectbox("Bot:", [f"{b[0]} ({b[3]})" for b in bots_con_pasos], key="diag_bot")
                correo_prueba = st.text_input("Correo de prueba:", key="diag_bot_correo").strip().lower()
                if st.button("Ejecutar prueba", key="diag_bot_btn") and correo_prueba:
                    bot_t = bots_con_pasos[[f"{b[0]} ({b[3]})" for b in bots_con_pasos].index(bot_sel)]
                    with st.spinner("Ejecutando los pasos..."):
                        hist_t = correr_con_turno(ejecutar_receta, bot_t[1], bot_t[0], [p for p in str(bot_t[2]).splitlines() if p.strip()], correo_prueba, defecto=AVISO_OCUPADO)
                    for m_t in hist_t:
                        with st.chat_message(m_t["role"]):
                            st.write(m_t["content"])
                            if m_t.get("botones"):
                                st.caption("Botones: " + " | ".join(m_t["botones"]))

            st.markdown("---")
            st.subheader("🎙️ Grabar los pasos de un bot")
            st.caption("Haz el trámite una vez, como lo haces tú hoy. La página anota cada botón que pulses y cada texto que escribas, y lo guarda como pasos automáticos para tus clientes.")
            if not bots_t:
                st.info("Primero agrega un bot en 'Fuentes de Extracción'.")
            else:
                nombres_b = [f"{b[0]} ({b[3]})" for b in bots_t]
                bot_g = st.selectbox("Bot a grabar:", nombres_b, key="rec_bot_sel")
                fila_bot = bots_t[nombres_b.index(bot_g)]
                correo_g = st.text_input("Correo de prueba (cuando lo escribas en el bot, se guardará como {correo}):", key="rec_correo").strip().lower()
                if st.button("▶️ Empezar grabación", key="rec_inicio"):
                    with st.spinner("Conectando con el bot (hasta 40 segundos)..."):
                        res_g = correr_con_turno(iniciar_grabacion, fila_bot[1], fila_bot[0], defecto=None, espera_max=20, duracion_max=40)
                    if not res_g:
                        st.error("No se pudo conectar: Telegram tardó demasiado o hay otra consulta en curso. Espera 1 minuto y vuelve a intentar.")
                    elif res_g[0] is None:
                        st.error(res_g[1])
                    else:
                        st.session_state['rec'] = {"bot": fila_bot[4], "min": res_g[0], "pasos": ["/start"], "hist": res_g[1]}
                        st.rerun()
                rec = st.session_state.get('rec')
                if rec and rec["bot"] == fila_bot[4]:
                    st.markdown("**Conversación con el bot:**")
                    for m_g in rec["hist"]:
                        with st.chat_message(m_g["role"]):
                            st.write(m_g["content"])
                    botones_g = next((m_g["botones"] for m_g in reversed(rec["hist"]) if m_g["role"] == "assistant" and m_g.get("botones")), [])
                    n_paso = len(rec["pasos"])
                    if botones_g:
                        st.write("👉 Pulsa el botón que pulsarías tú:")
                        cols_g = st.columns(min(len(botones_g), 2))
                        for i_b, txt_b in enumerate(botones_g):
                            if cols_g[i_b % 2].button(txt_b, key=f"rec_b_{n_paso}_{i_b}", use_container_width=True):
                                with st.spinner("Pulsando..."):
                                    rec["hist"] = correr_con_turno(enviar_y_recibir_bot, fila_bot[1], fila_bot[0], f"[BOTON]{txt_b}", rec["min"], defecto=AVISO_OCUPADO)
                                rec["pasos"].append(f"[BOTON]{txt_b}")
                                st.rerun()
                    txt_g = st.text_input("O escribe un texto al bot (por ejemplo, el correo de prueba):", key=f"rec_t_{n_paso}")
                    if st.button("Enviar texto", key=f"rec_tb_{n_paso}") and txt_g.strip():
                        with st.spinner("Enviando..."):
                            rec["hist"] = correr_con_turno(enviar_y_recibir_bot, fila_bot[1], fila_bot[0], txt_g.strip(), rec["min"], defecto=AVISO_OCUPADO)
                        rec["pasos"].append("{correo}" if (correo_g and txt_g.strip().lower() == correo_g) else txt_g.strip())
                        st.rerun()
                    st.markdown("**Pasos grabados hasta ahora:**")
                    st.code("\n".join(rec["pasos"]), language="markdown")
                    st.caption("Si el bot tarda en responder en algún paso, puedes agregar una línea [ESPERA]10 en 'Pasos automáticos' para que espere 10 segundos.")
                    cg1, cg2 = st.columns(2)
                    if cg1.button("💾 Guardar como pasos de este bot", type="primary", key="rec_guardar"):
                        c.execute("UPDATE bots_telegram SET recipe_steps=%s WHERE id=%s", ("\n".join(rec["pasos"]), fila_bot[4]))
                        conn.commit()
                        st.session_state['rec'] = None
                        st.success("¡Pasos guardados! Ya puedes probarlos arriba con 'Ejecutar prueba'.")
                    if cg2.button("🗑️ Descartar grabación", key="rec_descartar"):
                        st.session_state['rec'] = None
                        st.rerun()
        conn.close()

# ==========================================
# PANEL CLIENTE
# ==========================================
elif opcion == "Panel Cliente":
    st.header("📺 Buscador de Códigos")
    
    if 'cliente_logueado' not in st.session_state: st.session_state['cliente_logueado'] = False

    if not st.session_state['cliente_logueado']:
        with st.form("login_cliente"):
            u_l = st.text_input("Mi Usuario")
            p_l = st.text_input("Mi Clave", type="password")
            if st.form_submit_button("Entrar"):
                conn = psycopg2.connect(DB_URL)
                c = conn.cursor()
                c.execute("SELECT id, vendedor_id, estado_pago, usuario_cliente, correos_permitidos FROM cuentas WHERE usuario_cliente=%s AND pass_cliente=%s", (u_l, p_l))
                res = c.fetchone()
                conn.close()
                if res:
                    if res[2] == 0: st.error("Suscripción inactiva.")
                    else:
                        st.session_state['cliente_logueado'] = True
                        st.session_state['vendedor_id'] = res[1]
                        st.session_state['nombre_cli'] = res[3]
                        st.session_state['correos_permitidos'] = res[4] if res[4] else ""
                        st.rerun()
                else: st.error("Datos incorrectos.")
    
    else:
        st.success(f"Hola, {st.session_state['nombre_cli']}.")
        if st.button("Cerrar Sesión"):
            st.session_state['cliente_logueado'] = False
            st.session_state['modo_chat'] = False
            st.rerun()

        st.markdown("---")
        plat = st.selectbox("Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
        correo_buscar = st.text_input("Correo de streaming:")
        
        tipo_solicitud_cliente = None
        if plat in ("Disney+", "Prime Video"):
            st.info("1️⃣ En tu TV o celular, pide el código de acceso.  \n2️⃣ Escribe aquí el correo de la cuenta.  \n3️⃣ Pulsa el botón y copia tu código.")
        if plat == "Netflix":
            tipo_solicitud_cliente = st.radio("¿Qué buscas?", ["Inicio de Sesión (Nuevo dispositivo)", "Acceso Temporal (Viaje / Hogar)"])
        
        if st.button("Obtener mi código" if plat in ("Disney+", "Prime Video") else "Extraer Código"):
            st.session_state['modo_chat'] = False 
            st.session_state['sin_codigo_plat'] = None
            st.session_state['res_receta'] = None
            
            if correo_buscar:
                correo_limpio = correo_buscar.strip().lower()
                correos_autorizados = [e.strip().lower() for e in st.session_state.get('correos_permitidos', '').split(',')]
                
                if correo_limpio not in correos_autorizados:
                    st.info(f"Escaneando servidores en busca de correos para: **{correo_buscar}**")
                    with st.spinner('Buscando...'):
                        time.sleep(2)
                    st.error("No se encontró el código solicitado. Verifica que el correo original esté bien escrito o intenta de nuevo en unos minutos.")
                
                else:
                    conn = psycopg2.connect(DB_URL)
                    c = conn.cursor()
                    v_id = st.session_state['vendedor_id']
                    c.execute("SELECT correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal FROM correos_madre WHERE vendedor_id=%s", (v_id,))
                    correos_vendedor = c.fetchall()
                    c.execute("SELECT bot_username, string_session, recipe_steps, plataforma FROM bots_telegram WHERE vendedor_id=%s", (v_id,))
                    bots_vendedor = c.fetchall()
                    conn.close()

                    st.info(f"Escaneando servidores en busca de correos para: **{correo_buscar}**")
                    codigo_encontrado = None
                    
                    with st.spinner('Buscando...'):
                        for madre in ([] if plat == "Disney+" else correos_vendedor):
                            if not codigo_encontrado:
                                resultado = obtener_codigo_centralizado(madre[0], madre[1], correo_buscar, plat, madre[2], madre[3], madre[4], tipo_solicitud_cliente)
                                if resultado: codigo_encontrado = resultado
                        
                        if not codigo_encontrado and plat != "Prime Video":
                            bot_encontrado = None
                            for bot in bots_vendedor:
                                if bot[3] == "Todas las plataformas" or bot[3] == plat:
                                    bot_encontrado = bot
                                    break
                            
                            if bot_encontrado:
                                pasos_receta = [p for p in str(bot_encontrado[2] or "").splitlines() if p.strip()] if bot_encontrado[3] == plat else []
                                if pasos_receta:
                                    hist_r = correr_con_turno(ejecutar_receta, bot_encontrado[1], bot_encontrado[0], pasos_receta, correo_limpio, defecto=AVISO_OCUPADO)
                                    st.session_state['res_receta'] = {"historial": hist_r, "bot": bot_encontrado}
                                    st.rerun()
                                st.session_state['res_receta'] = {"historial": [{"id": 0, "role": "assistant", "content": "Error: este servicio todavía no está configurado.", "botones": []}], "bot": bot_encontrado}
                                st.rerun()

                    if codigo_encontrado:
                        codigo_str = str(codigo_encontrado)
                        if "BLOQUEADO" in codigo_str: 
                            st.error(codigo_str)
                        elif codigo_str.isdigit() or len(codigo_str) < 150: 
                            st.success("✅ ¡Código encontrado!")
                            st.markdown(f"<div style='text-align: center; border: 2px dashed #4CAF50; padding: 20px; border-radius: 10px; background-color: #1E1E1E;'><h2 style='color: #FFFFFF; margin:0;'>{codigo_str}</h2></div>", unsafe_allow_html=True)
                        else:
                            st.success("✅ ¡Acceso encontrado!")
                            html_seguro = f'<base target="_blank"><div style="background-color: #FFFFFF; color: #000000; padding: 10px;">{codigo_str}</div>'
                            st.components.v1.html(html_seguro, height=600, scrolling=True)
                    elif plat == "Prime Video":
                        st.warning("Todavía no llega tu código. Pídelo de nuevo en la app, espera 1 minuto y pulsa el botón otra vez. (Los códigos duran pocos minutos.)")
                    elif not st.session_state.get('modo_chat'): 
                        st.error("No se encontró el código solicitado. Revisa el correo original.")
            else:
                st.warning("Por favor, ingresa el correo de streaming.")

        res_receta = st.session_state.get('res_receta')
        if res_receta and not st.session_state.get('modo_chat'):
            hist_res = res_receta["historial"]
            respuestas = [m["content"] for m in hist_res if m["role"] == "assistant" and not m["content"].startswith("🤖 [Menú")]
            st.markdown("---")
            falla = (not respuestas) or any(x in respuestas[-1] for x in ("Hay muchos clientes", "Error de conexión", "Error: este servicio"))
            if falla:
                st.warning("No pudimos obtener tu código en este momento. Espera 1 minuto e inténtalo de nuevo. Si sigue igual, escríbele a tu proveedor.")
            else:
                texto_final = respuestas[-1]
                cod_r = re.search(r'\b\d{4,8}\b', texto_final)
                if cod_r:
                    st.success("✅ ¡Listo! Este es tu código:")
                    st.markdown(f"<div style='text-align: center; border: 2px dashed #4CAF50; padding: 20px; border-radius: 10px; background-color: #1E1E1E;'><h2 style='color: #FFFFFF; margin:0;'>{cod_r.group(0)}</h2></div>", unsafe_allow_html=True)
                    with st.expander("Ver el mensaje completo"):
                        st.write(texto_final)
                else:
                    st.info("Respuesta del sistema:")
                    st.write(texto_final)
