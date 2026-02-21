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
import streamlit.components.v1 as components  # Para renderizado seguro de HTML

# --- CONSTANTES ---
MI_API_ID = 34062718  
MI_API_HASH = 'ca9d5cbc6ce832c6660f949a5567a159'

# --- 1. CONFIGURACIÓN DE BASE DE DATOS ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix_v6.db')
    c = conn.cursor()
    # Tablas existentes (Vendedores, Correos Madre, Bots, Cuentas)
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT UNIQUE, clave TEXT, estado INTEGER, fecha_vencimiento DATE)''')
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

# --- 2. LÓGICA DE EXTRACCIÓN REFACTORIZADA (DRY & HYBRID) ---
def obtener_contenido_email(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):
    """
    Función Unificada: Extrae el código específico Y el cuerpo HTML para previsualización.
    """
    try:
        mail = imaplib.IMAP4_SSL(imap_serv)
        mail.login(email_madre, pass_app_madre)
        mail.select("inbox")
        
        # Criterio mejorado: Filtramos por remitente (seguridad) y destinatario
        sender = "info@account.netflix.com" if "Netflix" in plataforma else "amazon.com"
        criterio = f'(FROM "{sender}" TO "{email_cliente_final}")'
        
        status, mensajes = mail.search(None, criterio)
        if not mensajes[0]: return None 

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
        cuerpo_lower = cuerpo_html.lower()
        if ("inicio de sesión" in cuerpo_lower or "nuevo dispositivo" in cuerpo_lower) and not filtro_login:
            return {"error": "Acceso de Nuevo Dispositivo bloqueado por el vendedor."}
        if ("temporal" in cuerpo_lower or "viaje" in cuerpo_lower) and not filtro_temporal:
            return {"error": "Acceso Temporal bloqueado por el vendedor."}

        # --- EXTRACCIÓN DE CÓDIGO (Lógica del Fragmento A optimizada) ---
        codigo_detectado = "No detectado"
        if "Prime" in plataforma:
            match = re.search(r'(\d{6})', cuerpo_html)
            if match: codigo_detectado = match.group(1)
        else:
            # Para Netflix, buscamos el link y simulamos click para obtener el código de 4 dígitos
            links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo_html)
            link_n = [l for l in links if "update-primary-location" in l or "nm-c.netflix.com" in l]
            if link_n:
                try:
                    resp = requests.get(link_n[0], timeout=5)
                    nums = [n for n in re.findall(r'\b\d{4}\b', resp.text) if n not in ["2024", "2025", "2026"]]
                    if nums: codigo_detectado = nums[0]
                except: pass

        return {"html": cuerpo_html, "codigo": codigo_detectado}

    except Exception as e:
        return {"error": f"Error de conexión: {str(e)}"}
    finally:
        try: mail.logout()
        except: pass

# --- 3. INTERFAZ UI (FRAGMENTO A + INTEGRACIÓN B) ---
st.set_page_config(page_title="Gestión Pro v6.0", layout="centered")

# Inyección de CSS para consistencia visual y responsividad
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stButton>button { width: 100%; border-radius: 8px; font-weight: 600; }
    .email-preview-container { 
        border: 1px solid #ddd; 
        border-radius: 10px; 
        padding: 10px; 
        background: white;
        overflow-x: auto;
    }
    @media (max-width: 640px) {
        .reportview-container .main .block-container { padding: 1rem; }
    }
    </style>
    """, unsafe_allow_html=True)

menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Navegación", menu)

# ... (Lógica de Sesión y Panel Administrador/Vendedor se mantienen igual que en Fragmento A) ...

# ==========================================
# PANEL CLIENTE (INTEGRACIÓN FINAL)
# ==========================================
if opcion == "Panel Cliente":
    st.header("📲 Centro de Ayuda al Cliente")
    
    with st.container():
        u_c = st.text_input("Tu Usuario")
        p_c = st.text_input("Tu Contraseña", type="password")
        
        if st.button("Consultar mi Código"):
            conn = sqlite3.connect('gestion_netflix_v6.db')
            # Verificamos cliente y su estado de pago
            cliente = conn.execute("""SELECT vendedor_id, estado_pago FROM cuentas 
                                     WHERE usuario_cliente=? AND pass_cliente=?""", (u_c, p_c)).fetchone()
            
            if cliente:
                v_id, pagado = cliente
                if pagado:
                    # Buscamos en las fuentes del vendedor
                    fuentes = conn.execute("SELECT * FROM correos_madre WHERE vendedor_id=?", (v_id,)).fetchall()
                    
                    encontrado = False
                    for f in fuentes:
                        with st.spinner(f"Buscando en servidor {f[4]}..."):
                            # Usamos la nueva función del Fragmento B integrada
                            resultado = obtener_contenido_email(f[2], f[3], u_c, "Netflix", f[4], f[5], f[6])
                            
                            if resultado and "error" in resultado:
                                st.error(resultado["error"])
                                encontrado = True
                                break
                            elif resultado:
                                st.success(f"✅ Código encontrado: {resultado['codigo']}")
                                
                                # --- UI UX: Previsualización ---
                                with st.expander("👁️ Ver correo original (Previsualización)"):
                                    st.info("A continuación se muestra el correo oficial recibido:")
                                    components.html(resultado["html"], height=400, scrolling=True)
                                
                                encontrado = True
                                break
                    
                    if not encontrado:
                        st.warning("No se encontró ningún correo reciente. Reintenta en 1 minuto.")
                else:
                    st.error("Tu acceso está suspendido por falta de pago.")
            else:
                st.error("Credenciales inválidas.")
            conn.close()

# ... (El resto del Fragmento A se mantiene para las gestiones de Vendedor)
