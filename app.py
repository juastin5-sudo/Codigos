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

# --- DB ---
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


# --- BOT TELEGRAM ---
async def ejecutar_receta_bot(session_str, bot_username, receta_text, email_cliente, modo_test=False):

    try:

        async with TelegramClient(StringSession(session_str), MI_API_ID, MI_API_HASH) as client:

            await client.send_message(bot_username, email_cliente)

            await asyncio.sleep(4) 

            ultimos_msgs = await client.get_messages(bot_username, limit=1)

            return ultimos_msgs[0].text if ultimos_msgs else None

    except Exception as e:

        return f"Error con Bot: {str(e)}"



# --- FUNCION CENTRAL ---
def obtener_codigo_centralizado(email_madre, pass_app_madre, email_cliente_final, plataforma, imap_serv, filtro_login, filtro_temporal):

    try:

        mail = imaplib.IMAP4_SSL(imap_serv)

        mail.login(email_madre, pass_app_madre)

        mail.select("inbox")


        # REMITENTE SEGUN PLATAFORMA

        if plataforma == "Prime Video":

            criterio = f'(FROM "amazon.com" TO "{email_cliente_final}")'


        elif plataforma == "Disney+":

            criterio = f'(FROM "disneyplus.com" TO "{email_cliente_final}")'


        else:

            criterio = f'(FROM "info@account.netflix.com" TO "{email_cliente_final}")'



        status, mensajes = mail.search(None, criterio)


        if not mensajes[0]:

            return None


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



        cuerpo_lower = cuerpo.lower()



        es_login = (

            "code" in cuerpo_lower
            or "código" in cuerpo_lower
            or "login" in cuerpo_lower
            or "sign-in" in cuerpo_lower

        )


        es_temporal = (

            "temporal" in cuerpo_lower
            or "travel" in cuerpo_lower
            or "viaje" in cuerpo_lower
            or "update-primary-location" in cuerpo_lower

        )



        if es_login and not filtro_login:

            return "BLOQUEADO LOGIN"


        if es_temporal and not filtro_temporal:

            return "BLOQUEADO TEMPORAL"



        # AMAZON

        if plataforma == "Prime Video":

            match = re.search(r'\b(\d{6})\b', cuerpo)

            if match:

                return match.group(1)

            return None



        # DISNEY

        if plataforma == "Disney+":


            match = re.search(r'\b(\d{6})\b', cuerpo)

            if match:

                return match.group(1)

            return None



        # NETFLIX LOGIN

        match_login = re.search(r'\b(\d{6})\b', cuerpo)

        if match_login:

            return match_login.group(1)



        # NETFLIX TEMPORAL

        links = re.findall(r'href=[\'"]?([^\'" >]+)', cuerpo)


        link_n = [

            l for l in links

            if "update-primary-location" in l
            or "nm-c.netflix.com" in l

        ]


        if not link_n:

            return None


        resp = requests.get(link_n[0])


        nums = re.findall(r'\b\d{4}\b', resp.text)


        for n in nums:

            if n not in ["2024","2025","2026"]:

                return n


        return None


    except:

        return None




# --- STREAMLIT UI ---

st.set_page_config(page_title="Gestión de Cuentas v7", layout="centered")


menu = ["Panel Cliente", "Panel Vendedor", "Administrador"]


opcion = st.sidebar.selectbox("Navegación", menu)



# PANEL CLIENTE

if opcion == "Panel Cliente":

    st.title("Cliente")


    email_cliente = st.text_input("Tu correo")


    plataforma = st.selectbox(

        "Plataforma",

        ["Netflix","Prime Video","Disney+"]

    )


    if st.button("Obtener Codigo"):


        conn = sqlite3.connect('gestion_netflix_v6.db')

        c = conn.cursor()


        c.execute("""

        SELECT correo_imap,password_app,servidor_imap,filtro_login,filtro_temporal

        FROM correos_madre LIMIT 1

        """)


        datos = c.fetchone()

        conn.close()


        if datos:


            codigo = obtener_codigo_centralizado(

                datos[0],
                datos[1],
                email_cliente,
                plataforma,
                datos[2],
                datos[3],
                datos[4]

            )


            if codigo:

                st.success(f"Codigo: {codigo}")

            else:

                st.error("No encontrado")



# PANEL VENDEDOR

elif opcion == "Panel Vendedor":

    st.title("Vendedor")



# PANEL ADMIN

elif opcion == "Administrador":

    st.title("Admin")




