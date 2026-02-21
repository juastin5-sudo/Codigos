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
from bs4 import BeautifulSoup  # Para limpiar la previsualización

# --- CONSTANTES ---
MI_API_ID = 34062718  
MI_API_HASH = 'ca9d5cbc6ce832c6660f949a5567a159'

# --- 1. CONFIGURACIÓN DE BASE DE DATOS (V6) ---
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

# --- FUNCIÓN AUXILIAR PARA PREVISUALIZACIÓN ---
def limpiar_cuerpo_preview(html_content):
    """Extrae texto limpio de un HTML para la previsualización."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        texto = soup.get_text(separator=' ')
        # Limpiar espacios extra y saltos
        texto_limpio = re.sub(r'\s+', ' ', texto).strip()
        return texto_limpio[:250] + "..." # Retornamos solo un fragmento
    except:
        return html_content[:250]

# --- LÓGICA DE EXTRACCIÓN: BOT DE TELEGRAM ---
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):
    session_str = session_str.strip()
    try:
        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:
            await client.send_message(bot_username, email_cliente) 
            await asyncio.sleep(4) 
            
            ultimos_msgs = await client.get_messages(bot_username, limit=1)
            respuesta = ultimos_msgs[0].text if ultimos_msgs else "Sin respuesta del bot."
            # Los bots suelen dar el código directo, lo tratamos como resultado plano
            return {"codigo": respuesta, "preview": "Extraído vía Telegram Bot"}
    except Exception as e:
        return f"Error con Bot: {str(e)}"

# --- LÓGICA DE EXTRACCIÓN: CORREOS (IMAP) CON FILTROS ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")' if plataforma == "Prime Video" else f'(FROM "info@account.netflix.com" TO "{email_cliente_final}")'
        status, mensajes = mail.search(None, criterio)
        if not mensajes[0]: return None 
        
        ultimo_id = mensajes[0].split()[-1]
        res, datos = mail.fetch(ultimo_id, '(RFC822)')
        msg = email.message_from_bytes(datos[0][1])
        
        # Extraer Asunto para la previsualización
        asunto = email.header.decode_header(msg.get("Subject"))[0][0]
        if isinstance(asunto, bytes): asunto = asunto.decode()

        cuerpo = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ["text/plain", "text/html"]:
                    cuerpo += part.get_payload(decode=True).decode('utf-8', errors='ignore')
        else:
            cuerpo = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

        # Filtros de seguridad (Tu lógica original intacta)
        es_login = "inicio de sesión" in cuerpo.lower() or "nuevo dispositivo" in cuerpo.lower()
        es_temporal = "temporal" in cuerpo.lower() or "viaje" in cuerpo.lower() or "travel" in cuerpo.lower()

        if es_login and not filtro_login:
            return {"error": "BLOQUEADO: El vendedor desactivó la entrega automática para Inicios de Sesión."}
        if es_temporal and not filtro_temporal:
            return {"error": "BLOQUEADO: El vendedor desactivó la entrega automática para Accesos Temporales."}

        preview_texto = f"📬 {asunto}\n\n{limpiar_cuerpo_preview(cuerpo)}"

        codigo = None
        if plataforma == "Prime Video":
            match = re.search(r'c(?:o|ó)digo de verificaci(?:o|ó)n es:\s*(\d{6})', cuerpo, re.IGNORECASE)
            codigo = match.group(1) if match else None
        else:
            links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo)
            link_n = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l]
            if link_n:
                resp = requests.get(link_n[0])
                nums = [n for n in re.findall(r'\b\d{4}\b', resp.text) if n not in ["2024", "2025", "2026"]]
                codigo = nums[0] if nums else None

        if codigo:
            return {"codigo": codigo, "preview": preview_texto}
        return None
    except Exception as e:
        return None 

# --- INTERFAZ DE USUARIO ---
st.set_page_config(page_title="Gestión de Cuentas v6.0", layout="centered")
menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Navegación", menu)

# --- INICIALIZACIÓN DE VARIABLES DE SESIÓN ---
for key, val in [('admin_logueado', False), ('vendedor_logueado', False), ('id_vend_actual', None), ('nombre_vend_actual', ""), ('cliente_logueado', False)]:
    if key not in st.session_state: st.session_state[key] = val

# ==========================================
# PANEL ADMINISTRADOR
# ==========================================
if opcion == "Administrador":
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
            nv, cv = st.text_input("Usuario"), st.text_input("Contraseña")
            if st.button("Guardar Vendedor") and nv and cv:
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
                c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1])
                c1.write(f"**{v[1]}** (Pass: `{v[2]}`)")
                c2.write("🟢 Activo" if v[3] else "🔴 Inactivo")
                if c3.button("Estado", key=f"v_stat_{v[0]}"):
                    conn.execute("UPDATE vendedores SET estado=? WHERE id=?", (0 if v[3] else 1, v[0]))
                    conn.commit()
                    st.rerun()
                if c4.button("🗑️", key=f"v_del_{v[0]}"):
                    conn.execute("DELETE FROM correos_madre WHERE vendedor_id=?", (v[0],)); conn.execute("DELETE FROM bots_telegram WHERE vendedor_id=?", (v[0],)); conn.execute("DELETE FROM cuentas WHERE vendedor_id=?", (v[0],)); conn.execute("DELETE FROM vendedores WHERE id=?", (v[0],))
                    conn.commit(); st.rerun()
            conn.close()

# ==========================================
# PANEL VENDEDOR
# ==========================================
elif opcion == "Panel Vendedor":
    st.header("👨‍💼 Portal de Vendedores")
    if not st.session_state['vendedor_logueado']:
        with st.form("form_login_vendedor"):
            u_v, p_v = st.text_input("Usuario"), st.text_input("Clave", type="password")
            if st.form_submit_button("Iniciar Sesión"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                vend = conn.execute("SELECT id, estado, usuario FROM vendedores WHERE usuario=? AND clave=?", (u_v, p_v)).fetchone()
                conn.close()
                if vend:
                    if vend[1] == 1:
                        st.session_state.update({'vendedor_logueado': True, 'id_vend_actual': vend[0], 'nombre_vend_actual': vend[2]})
                        st.rerun()
                    else: st.error("Cuenta desactivada.")
                else: st.error("Credenciales incorrectas.")
    else:
        st.success(f"Bienvenido, {st.session_state['nombre_vend_actual']}")
        if st.button("🚪 Cerrar Sesión"):
            st.session_state['vendedor_logueado'] = False; st.rerun()
        st.markdown("---")
        v_id = st.session_state['id_vend_actual']
        conn = sqlite3.connect('gestion_netflix_v6.db')
        c = conn.cursor()
        tab_fuentes, tab_clientes = st.tabs(["⚙️ Fuentes", "👥 Clientes"])
        with tab_fuentes:
            st.subheader("📧 Mis Correos")
            with st.form("f_madre"):
                tipo_correo = st.radio("Proveedor:", ["Gmail / Google Workspace", "Webmail", "Outlook / Hotmail"])
                me, mp = st.text_input("Correo"), st.text_input("Clave App", type="password")
                serv = "imap.gmail.com"
                if tipo_correo == "Webmail": serv = st.text_input("Servidor IMAP", value="mail.tudominio.com")
                elif tipo_correo == "Outlook / Hotmail": serv = "outlook.office365.com"
                f_log, f_tmp = st.checkbox("Permitir Inicio Sesión", value=True), st.checkbox("Permitir Temporal", value=True)
                if st.form_submit_button("Añadir"):
                    c.execute("INSERT INTO correos_madre (vendedor_id, correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal) VALUES (?,?,?,?,?,?)", (v_id, me, mp, serv, int(f_log), int(f_tmp)))
                    conn.commit(); st.rerun()
            for cg in c.execute("SELECT id, correo_imap, servidor_imap FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall():
                cc1, cc2 = st.columns([5, 1])
                cc1.caption(f"✅ {cg[1]}")
                if cc2.button("🗑️", key=f"del_cm_{cg[0]}"):
                    c.execute("DELETE FROM correos_madre WHERE id=?", (cg[0],)); conn.commit(); st.rerun()

        with tab_clientes:
            with st.form("f_cli"):
                c_u, c_p = st.text_input("Usuario web"), st.text_input("Clave web")
                if st.form_submit_button("Registrar Cliente"):
                    try:
                        c.execute("INSERT INTO cuentas (usuario_cliente, pass_cliente, vendedor_id) VALUES (?,?,?)", (c_u, c_p, v_id))
                        conn.commit(); st.success("Creado."); st.rerun()
                    except: st.error("Ya existe.")
            for cli in c.execute("SELECT id, usuario_cliente, estado_pago, pass_cliente FROM cuentas WHERE vendedor_id=?", (v_id,)).fetchall():
                cc1, cc2, cc3 = st.columns([3, 1.5, 0.5])
                cc1.write(f"👤 {cli[1]} | 🔑 `{cli[3]}`")
                if cc2.button("🟢 Activo" if cli[2] else "🔴 Vencido", key=f"pago_{cli[0]}"):
                    c.execute("UPDATE cuentas SET estado_pago=? WHERE id=?", (0 if cli[2] else 1, cli[0]))
                    conn.commit(); st.rerun()
                if cc3.button("🗑️", key=f"del_cli_{cli[0]}"):
                    c.execute("DELETE FROM cuentas WHERE id=?", (cli[0],)); conn.commit(); st.rerun()
        conn.close()

# ==========================================
# PANEL CLIENTE (CON INTEGRACIÓN DE PREVIEW)
# ==========================================
elif opcion == "Panel Cliente":
    st.header("📺 Buscador de Códigos")
    if not st.session_state['cliente_logueado']:
        with st.form("login_cliente"):
            u_l, p_l = st.text_input("Usuario"), st.text_input("Clave", type="password")
            if st.form_submit_button("Entrar"):
                conn = sqlite3.connect('gestion_netflix_v6.db')
                res = conn.execute("SELECT id, vendedor_id, estado_pago, usuario_cliente FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?", (u_l, p_l)).fetchone()
                conn.close()
                if res:
                    if res[2] == 0: st.error("Suscripción inactiva.")
                    else:
                        st.session_state.update({'cliente_logueado': True, 'vendedor_id': res[1], 'nombre_cli': res[3]})
                        st.rerun()
                else: st.error("Incorrecto.")
    else:
        st.success(f"Hola, {st.session_state['nombre_cli']}.")
        if st.button("Cerrar Sesión"): st.session_state['cliente_logueado'] = False; st.rerun()
        st.markdown("---")
        plat = st.selectbox("Plataforma", ["Netflix", "Prime Video", "Disney+", "Otros"])
        correo_buscar = st.text_input("Correo de la cuenta de streaming:")
        
        if st.button("Extraer Código"):
            if correo_buscar:
                st.info(f"Buscando para: {correo_buscar}...")
                conn = sqlite3.connect('gestion_netflix_v6.db')
                v_id = st.session_state['vendedor_id']
                correos_vendedor = conn.execute("SELECT correo_imap, password_app, servidor_imap, filtro_login, filtro_temporal FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()
                bots_vendedor = conn.execute("SELECT bot_username, string_session, recipe_steps, plataforma FROM bots_telegram WHERE vendedor_id=?", (v_id,)).fetchall()
                conn.close()

                resultado_final = None
                with st.spinner('Escaneando buzones...'):
                    for madre in correos_vendedor:
                        if not resultado_final:
                            res = obtener_codigo_centralizado(madre[0], madre[1], correo_buscar, plat, madre[2], madre[3], madre[4])
                            if res: resultado_final = res
                    
                    if not resultado_final:
                        for bot in bots_vendedor:
                            if not resultado_final and (bot[3] == "Todas las plataformas" or bot[3] == plat):
                                res = asyncio.run(ejecutar_receta_bot(bot[1], bot[0], bot[2], correo_buscar))
                                if isinstance(res, dict): resultado_final = res

                if resultado_final:
                    if "error" in resultado_final:
                        st.error(resultado_final["error"])
                    else:
                        st.balloons()
                        st.success("✅ ¡Código extraído!")
                        # Mostrar el código grande
                        st.markdown(f"<div style='text-align: center; border: 2px dashed #4CAF50; padding: 20px; border-radius: 10px;'><h1 style='color: #E50914; margin:0;'>{resultado_final['codigo']}</h1></div>", unsafe_allow_html=True)
                        
                        # Mostrar la previsualización del correo
                        with st.expander("📄 Ver previsualización del correo recibido"):
                            st.write(resultado_final["preview"])
                else:
                    st.error("No se encontró código reciente. Revisa el correo ingresado.")
            else: st.warning("Ingresa el correo.")



