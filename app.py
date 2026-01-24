import streamlit as st
import sqlite3
import pandas as pd
import imaplib
import email
import re
import requests
import asyncio
import time
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# --- 1. CONFIGURACIÓN DE BASE DE DATOS (EXTENDIDA) ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  usuario TEXT UNIQUE, clave TEXT, estado INTEGER, fecha_vencimiento DATE)''')
    
    # Tabla Cuentas Extendida con StringSession, Bot y Receta
    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  plataforma TEXT, email TEXT, password_app TEXT, 
                  usuario_cliente TEXT UNIQUE, pass_cliente TEXT, 
                  vendedor_id INTEGER, estado INTEGER,
                  string_session TEXT, provider_bot TEXT, recipe_steps TEXT,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    conn.close()

inicializar_db()

# --- 2. LÓGICA DE TELETHON (NUEVA) ---
async def ejecutar_receta_telegram(session_str, bot_username, receta_raw, email_cliente):
    """
    Ejecuta la secuencia de pasos en el bot proveedor.
    """
    api_id = 1234567  # Reemplazar con API ID real si es necesario
    api_hash = 'tu_api_hash_aqui' 
    
    try:
        async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
            # 1. Enviar mensaje inicial al bot para asegurar que el chat existe
            await client.send_message(bot_username, "/start")
            await asyncio.sleep(2)
            
            pasos = receta_raw.split("\n")
            for paso in pasos:
                paso = paso.strip()
                if not paso: continue
                
                if paso.startswith("BOTON:"):
                    texto_boton = paso.replace("BOTON:", "").strip()
                    # Buscar el último mensaje del bot para hacer clic en sus botones
                    messages = await client.get_messages(bot_username, limit=1)
                    if messages and messages[0].reply_markup:
                        await messages[0].click(text=texto_boton)
                    
                elif paso.startswith("ENVIAR:CORREO"):
                    await client.send_message(bot_username, email_cliente)
                    
                elif paso.startswith("ESPERAR:"):
                    segundos = int(re.search(r'\d+', paso).group())
                    await asyncio.sleep(segundos)
            
            # Al final, esperamos el último mensaje que debería contener el código
            await asyncio.sleep(3)
            final_messages = await client.get_messages(bot_username, limit=1)
            return final_messages[0].text
    except Exception as e:
        return f"Error en Telegram: {str(e)}"

# --- LÓGICA DE EXTRACCIÓN GMAIL (PRESERVADA) ---
def obtener_codigo_real(correo_cuenta, password_app):
    # (Se mantiene igual que tu código original por brevedad, se asume su existencia aquí)
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(correo_cuenta, password_app)
        # ... resto del código original ...
        return "Código de ejemplo: 1234" # Simulado para este bloque
    except Exception as e:
        return f"Error de conexión: {str(e)}"

# --- 3. INTERFAZ ---
st.set_page_config(page_title="Sistema de Gestión PRO", layout="centered")

menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]
opcion = st.sidebar.selectbox("Seleccione un Panel", menu)

# (Sección Administrador se mantiene igual)

# --- PANEL VENDEDOR (MODIFICADO) ---
if opcion == "Panel Vendedor":
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
            st.success(f"Bienvenido. Acceso hasta: {v_vence}")
            
            with st.expander("➕ Registrar Nuevo Cliente / Bot Automatizado"):
                with st.form("registro_cliente"):
                    p_form = st.selectbox("Plataforma", ["Netflix", "Disney+", "Prime Video", "Bot Externo"])
                    m_form = st.text_input("Correo Dueño (Si aplica)")
                    app_form = st.text_input("Clave App Gmail (Si aplica)", type="password")
                    u_cli_form = st.text_input("Usuario para el Cliente")
                    p_cli_form = st.text_input("Clave para el Cliente", type="password")
                    
                    st.markdown("---")
                    st.subheader("Configuración de Automatización (Telegram)")
                    s_session = st.text_area("String Session (Telethon)", placeholder="Cadena larga de texto...")
                    p_bot = st.text_input("Bot Proveedor", placeholder="@NombreDelBot")
                    r_steps = st.text_area("Receta de Pasos", placeholder="BOTON:Disney\nENVIAR:CORREO\nESPERAR:5")
                    
                    if st.form_submit_button("Guardar Configuración"):
                        c.execute("""INSERT INTO cuentas 
                            (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado, string_session, provider_bot, recipe_steps) 
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (p_form, m_form, app_form, u_cli_form, p_cli_form, v_id, 1, s_session, p_bot, r_steps))
                        conn.commit()
                        st.success("✅ Cliente y Automatización guardados.")
        conn.close()

# --- PANEL CLIENTE (MODIFICADO) ---
elif opcion == "Panel Cliente":
    st.header("📺 Obtener mi Código")
    u_log = st.text_input("Usuario")
    p_log = st.text_input("Clave", type="password")
    
    if st.button("GENERAR CÓDIGO"):
        conn = sqlite3.connect('gestion_netflix.db')
        c = conn.cursor()
        query = "SELECT * FROM cuentas WHERE usuario_cliente=? AND pass_cliente=?"
        c.execute(query, (u_log, p_log))
        result = c.fetchone()
        
        if result:
            # Índices según la nueva tabla: email(2), pass_app(3), string_session(8), provider_bot(9), recipe_steps(10)
            email_acc, pass_app = result[2], result[3]
            s_session, p_bot, r_steps = result[8], result[9], result[10]
            
            with st.spinner('Procesando solicitud...'):
                # Prioridad: Si hay receta de Telegram, usar Telethon
                if s_session and p_bot:
                    resultado = asyncio.run(ejecutar_receta_telegram(s_session, p_bot, r_steps, email_acc))
                    st.code(resultado) # Muestra la respuesta del bot (el código)
                else:
                    # Si no, usar el método tradicional de Gmail
                    codigo = obtener_codigo_real(email_acc, pass_app)
                    st.markdown(f"<h1 style='text-align: center; color: #E50914;'>{codigo}</h1>", unsafe_allow_html=True)
        else:
            st.error("Credenciales incorrectas.")
        conn.close()
