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
import streamlit.components.v1 as components

# --- CONSTANTES ---
MI_API_ID = 34062718  
MI_API_HASH = 'ca9d5cbc6ce832c6660f949a5567a159'

# --- 1. CONFIGURACIÓN DE BASE DE DATOS ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix_v6.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  usuario TEXT UNIQUE, clave TEXT, estado INTEGER, fecha_vencimiento DATE)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS correos_madre (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,
                 correo_imap TEXT, password_app TEXT, servidor_imap TEXT DEFAULT 'imap.gmail.com',
                 filtro_login INTEGER DEFAULT 1, filtro_temporal INTEGER DEFAULT 1,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS bots_telegram (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor_id INTEGER,
                 bot_username TEXT, plataforma TEXT, string_session TEXT, recipe_steps TEXT,
                 FOREIGN KEY (vendedor_id) REFERENCES vendedores(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_cliente TEXT UNIQUE, 
                  pass_cliente TEXT, vendedor_id INTEGER, estado_pago INTEGER DEFAULT 1,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    conn.close()

inicializar_db()

# --- 2. LÓGICA DE EXTRACCIÓN REFACTORIZADA (DRY) ---

async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):
    """Maneja la extracción vía Telegram Bot"""
    try:
        async with TelegramClient(StringSession(session_str.strip()), MI_API_ID, MI_API_HASH) as client:
            await client.send_message(bot_username, email_cliente)
            await asyncio.sleep(4) 
            ultimos_msgs = await client.get_messages(bot_username, limit=1)
            return ultimos_msgs[0].text if ultimos_msgs else "Sin respuesta del bot."
    except Exception as e:
        return f"Error con Bot: {str(e)}"

def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):
    """
    Integración Fragmento A + B:
    Extrae el código automáticamente y captura el cuerpo HTML para previsualización.
    """
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        
        # Criterio mejorado: Filtramos por destinatario y plataforma (Fragmento A)
        dominio_remitente = "amazon.com" if plataforma == "Prime Video" else "account.netflix.com"
        criterio = f'(FROM "{dominio_remitente}" TO "{email_cliente_final}")'
        
        status, mensajes = mail.search(None, criterio)
        if not mensajes[0]: return None, None
        
        ultimo_id = mensajes[0].split()[-1]
        res, datos = mail.fetch(ultimo_id, '(RFC822)')
        msg = email.message_from_bytes(datos[0][1])
        
        cuerpo_html = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    cuerpo_html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    break
        else:
            cuerpo_html = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

        # --- FILTROS DE SEGURIDAD ---
        es_login = any(x in cuerpo_html.lower() for x in ["inicio de sesión", "nuevo dispositivo", "new device"])
        es_temporal = any(x in cuerpo_html.lower() for x in ["temporal", "viaje", "travel"])

        if es_login and not filtro_login:
            return "BLOQUEADO: Inicios de Sesión desactivados.", cuerpo_html
        if es_temporal and not filtro_temporal:
            return "BLOQUEADO: Accesos Temporales desactivados.", cuerpo_html

        # --- EXTRACCIÓN AUTOMÁTICA DE CÓDIGO (Fragmento A) ---
        codigo_extraido = "No se detectó código numérico"
        if plataforma == "Prime Video":
            match = re.search(r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})', cuerpo_html, re.IGNORECASE)
            if match: codigo_extraido = match.group(1)
        else:
            links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo_html)
            link_n = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l]
            if link_n:
                resp = requests.get(link_n[0])
                nums = [n for n in re.findall(r'\b\d{4}\b', resp.text) if n not in ["2024", "2025", "2026"]]
                if nums: codigo_extraido = nums[0]

        return codigo_extraido, cuerpo_html

    except Exception as e:
        return f"Error: {str(e)}", None

# --- 3. INTERFAZ DE USUARIO (UI/UX OPTIMIZADA) ---

st.set_page_config(page_title="Gestión de Cuentas v6.0", layout="centered")

# CSS para consistencia y adaptabilidad móvil
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 8px; height: 3em; margin-bottom: 10px; }
    .stExpander { border: 1px solid #f0f2f6; border-radius: 8px; }
    @media (max-width: 640px) {
        .main .block-container { padding: 1rem; }
    }
    </style>
    """, unsafe_allow_html=True)

menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Navegación", menu)

# Sesiones
for key in ['admin_logueado', 'vendedor_logueado', 'id_vend_actual', 'nombre_vend_actual']:
    if key not in st.session_state:
        st.session_state[key] = False if 'logueado' in key else None

# ==========================================
# PANEL CLIENTE (Donde ocurre la magia de la previsualización)
# ==========================================
if opcion == "Panel Cliente":
    st.header("📱 Mi Acceso Directo")
    with st.container():
        u_cli = st.text_input("Usuario Web")
        p_cli = st.text_input("Clave", type="password")
        
        if st.button("Buscar mi Código"):
            conn = sqlite3.connect('gestion_netflix_v6.db')
            # Validamos cliente y obtenemos datos del vendedor asociado
            cliente_data = conn.execute('''SELECT vendedor_id, estado_pago FROM cuentas 
                                         WHERE usuario_cliente=? AND pass_cliente=?''', (u_cli, p_cli)).fetchone()
            
            if cliente_data:
                v_id, pagado = cliente_data
                if pagado:
                    # Buscamos en todas las fuentes del vendedor
                    fuentes = conn.execute("SELECT * FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()
                    encontrado = False
                    
                    for f in fuentes:
                        # f[2]=correo, f[3]=pass, f[4]=servidor, f[5]=f_login, f[6]=f_temp
                        res_cod, res_html = obtener_codigo_centralizado(f[2], f[3], u_cli, "Netflix", f[4], f[5], f[6])
                        
                        if res_cod:
                            st.success(f"### Tu código es: {res_cod}")
                            if res_html:
                                with st.expander("👁️ Ver correo original (Previsualización)"):
                                    components.html(res_html, height=400, scrolling=True)
                            encontrado = True
                            break
                    
                    if not encontrado:
                        st.info("No se encontró ningún correo reciente. Asegúrate de haber solicitado el código en la App de Streaming.")
                else:
                    st.error("Tu acceso está suspendido por falta de pago.")
            else:
                st.error("Credenciales de cliente incorrectas.")
            conn.close()

# ==========================================
# PANEL ADMINISTRADOR
# ==========================================
elif opcion == "Administrador":
    st.header("🔑 Panel de Control Maestro")
    
    if not st.session_state['admin_logueado']:
        with st.form("form_login_admin"):
            c_maestra = st.text_input("Clave Maestra", type="password")
            if st.form_submit_button("Ingresar"):
                if c_maestra == "merida2026":
                    st.session_state['admin_logueado'] = True
                    st.rerun()
                else: st.error("Clave incorrecta.")
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

        with col_lista:
            st.subheader("👥 Vendedores")
            conn = sqlite3.connect('gestion_netflix_v6.db')
            vendedores = conn.execute("SELECT * FROM vendedores").fetchall()
            for v in vendedores:
                with st.expander(f"👤 {v[1]}"):
                    c1, c2 = st.columns(2)
                    c1.write(f"Estado: {'🟢 Activo' if v[3] else '🔴 Inactivo'}")
                    if c2.button("Eliminar", key=f"del_v_{v[0]}"):
                        conn.execute("DELETE FROM vendedores WHERE id=?", (v[0],))
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
            if st.form_submit_button("Iniciar Sesión"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                vend = conn.execute("SELECT id, estado, usuario FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()
                conn.close()
                if vend and vend[1] == 1:
                    st.session_state['vendedor_logueado'] = True
                    st.session_state['id_vend_actual'] = vend[0]
                    st.session_state['nombre_vend_actual'] = vend[2]
                    st.rerun()
                else: st.error("Acceso denegado.")
    else:
        st.success(f"Bienvenido, {st.session_state['nombre_vend_actual']}")
        if st.button("🚪 Cerrar Sesión"):
            st.session_state['vendedor_logueado'] = False
            st.rerun()
            
        v_id = st.session_state['id_vend_actual']
        tab_fuentes, tab_clientes = st.tabs(["⚙️ Fuentes", "👥 Clientes"])
        
        with tab_fuentes:
            st.subheader("📧 Configurar Correo")
            with st.form("f_madre"):
                me = st.text_input("Correo")
                mp = st.text_input("Clave de Aplicación", type="password")
                srv = st.selectbox("Servidor", ["imap.gmail.com", "outlook.office365.com", "mail.dominio.com"])
                f_log = st.checkbox("Permitir Login", value=True)
                f_tmp = st.checkbox("Permitir Temporal", value=True)
                
                if st.form_submit_button("Añadir"):
                    conn = sqlite3.connect('gestion_netflix_v6.db')
                    conn.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal) VALUES (?,?,?,?,?,?)", 
                                 (v_id, me, mp, srv, int(f_log), int(f_tmp)))
                    conn.commit()
                    conn.close()
                    st.rerun()

        with tab_clientes:
            st.subheader("👥 Mis Clientes")
            conn = sqlite3.connect('gestion_netflix_v6.db')
            clientes = conn.execute("SELECT id, usuario_cliente, estado_pago FROM cuentas WHERE vendedor_id=?", (v_id,)).fetchall()
            for cl in clientes:
                c1, c2 = st.columns([3, 1])
                c1.write(f"{cl[1]} - {'✅ Pagado' if cl[2] else '❌ Pendiente'}")
                if c2.button("Cambiar", key=f"pay_{cl[0]}"):
                    conn.execute("UPDATE cuentas SET estado_pago=? WHERE id=?", (0 if cl[2] else 1, cl[0]))
                    conn.commit()
                    st.rerun()
            conn.close()

