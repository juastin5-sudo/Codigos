import streamlit as st
import sqlite3
import pandas as pd
import imaplib
import email
import re
import requests
from datetime import datetime, timedelta

# --- 1. CONFIGURACIÓN DE BASE DE DATOS ---
def inicializar_db():
    conn = sqlite3.connect('gestion_netflix.db')
    c = conn.cursor()
    # Tabla Vendedores
    c.execute('''CREATE TABLE IF NOT EXISTS vendedores 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  usuario TEXT UNIQUE, 
                  clave TEXT, 
                  estado INTEGER, 
                  fecha_vencimiento DATE)''')
    # Tabla Cuentas (Clientes)
    c.execute('''CREATE TABLE IF NOT EXISTS cuentas 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  plataforma TEXT, 
                  email TEXT, 
                  password_app TEXT, 
                  usuario_cliente TEXT UNIQUE, 
                  pass_cliente TEXT, 
                  vendedor_id INTEGER,
                  estado INTEGER,
                  FOREIGN KEY(vendedor_id) REFERENCES vendedores(id))''')
    conn.commit()
    conn.close()

inicializar_db()

# --- 2. LÓGICA DE EXTRACCIÓN DE CÓDIGO ---
def obtener_codigo_real(correo_cuenta, password_app):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(correo_cuenta, password_app)
        mail.select("inbox")
        
        # Criterio de búsqueda para Netflix
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

        # Simular clic en el botón
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
                    c.execute("INSERT INTO vendedores (usuario, clave, estado, fecha_vencimiento) VALUES (?,?,?,?)", 
                              (nuevo_v, clave_v, 1, vencimiento))
                    conn.commit()
                    st.success(f"Vendedor {nuevo_v} creado hasta {vencimiento}")
                except:
                    st.error("El usuario ya existe.")
                conn.close()

        st.subheader("Lista de Vendedores")
        conn = sqlite3.connect('gestion_netflix.db')
        # Traemos todos los campos, incluyendo la clave
        df_v = pd.read_sql_query("SELECT id, usuario, clave, estado, fecha_vencimiento FROM vendedores", conn)
        
        for index, row in df_v.iterrows():
            # Añadimos un contenedor para que se vea bien en celular
            with st.container():
                col1, col2, col3 = st.columns([2, 2, 1])
                
                with col1:
                    st.write(f"👤 **{row['usuario']}**")
                    # Mostramos la clave con un pequeño icono de llave
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
                st.markdown("---") # Línea divisoria entre vendedores
        conn.close()

# --- PANEL VENDEDOR ---
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
                    p_form = st.selectbox("Plataforma", ["Netflix", "Disney+", "Prime Video"])
                    m_form = st.text_input("Correo Netflix (Dueño)")
                    app_form = st.text_input("Clave Aplicación Gmail", type="password")
                    u_cli_form = st.text_input("Usuario para Cliente")
                    p_cli_form = st.text_input("Clave para Cliente", type="password")
                    
                    if st.form_submit_button("Guardar Cliente"):
                        try:
                            c.execute("INSERT INTO cuentas (plataforma, email, password_app, usuario_cliente, pass_cliente, vendedor_id, estado) VALUES (?,?,?,?,?,?,?)",
                                      (p_form, m_form, app_form, u_cli_form, p_cli_form, v_id, 1))
                            conn.commit()
                            st.success("✅ Cliente registrado con éxito.")
                        except:
                            st.error("Error: El nombre de usuario del cliente ya existe.")
                
                st.subheader("Mis Clientes")
                df_c = pd.read_sql_query(f"SELECT usuario_cliente, plataforma, email FROM cuentas WHERE vendedor_id={v_id}", conn)
                st.table(df_c)
        else:
            st.error("Credenciales incorrectas.")
        conn.close()

# --- PANEL CLIENTE ---
elif opcion == "Panel Cliente":
    st.header("📺 Obtener mi Código")
    st.info("Ingresa los datos proporcionados por tu vendedor.")
    
    u_log = st.text_input("Usuario Cliente")
    p_log = st.text_input("Contraseña Cliente", type="password")
    
    if st.button("GENERAR CÓDIGO"):
        if u_log and p_log:
            conn = sqlite3.connect('gestion_netflix.db')
            c = conn.cursor()
            # Unimos con vendedores para verificar que el vendedor no esté suspendido
            query = """
                SELECT cuentas.email, cuentas.password_app, vendedores.estado, vendedores.fecha_vencimiento 
                FROM cuentas 
                JOIN vendedores ON cuentas.vendedor_id = vendedores.id 
                WHERE cuentas.usuario_cliente=? AND cuentas.pass_cliente=?
            """
            c.execute(query, (u_log, p_log))
            result = c.fetchone()
            conn.close()
            
            if result:
                email_acc, pass_app, v_estado, v_vence = result
                v_vence_dt = datetime.strptime(v_vence, '%Y-%m-%d').date()
                
                if v_estado == 0 or v_vence_dt < datetime.now().date():
                    st.error("Servicio temporalmente inactivo (Vendedor no autorizado).")
                else:
                    with st.spinner('Extrayendo código de Netflix...'):
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

# --- PIE DE PÁGINA ---
st.sidebar.markdown("---")
st.sidebar.caption("Sistema v2.0 - 2026")
