# Holded New Orders Mailer

Script en Python que **detecta pedidos de venta (Sales Orders) en Holded** y envía un **correo de reserva de material** con una tabla de líneas.  
Funciona en local y/o de forma automática con **GitHub Actions** (cada 5 minutos), evitando duplicados con un **archivo de estado** (`.state/processed_salesorders.json`).

---

## 🚀 Qué hace

- Consulta pedidos de Holded (por `--doc-id`, por ventana de minutos/días o todo el año con `--ytd`).
- Detecta **pedidos nuevos** y **cambios de estado**:
  - Pendiente/Aceptado → Cancelado → envía email **CANCELADO**.
  - Cancelado → Pendiente/Aceptado → envía email de **VENDIDO (Reabierto)**.

- Mapea líneas de producto e infiere:
  - **Potencia (W)** desde atributos o texto (p. ej., `605W`, `A605`).
  - **Precio** dinámico:
    - Si hay potencia → **€/W**.
    - Si NO hay potencia → **€/ud**.
  - **Pallets** (solo si hay potencia), con reglas configurables.
- Detecta la línea de **transporte por nombre**: `Transporte`, `Shipping cost`, `Shipping`, `Transport`, `Flete`, `Portes`, `Envío`  
  (solo muestra transporte **en la primera fila**).
- Añade columna de Comercial en la tabla:
  - Se obtiene de los tags del producto o del pedido
  - Mapeo actual:
    - `paco` → Paco
    - `canet` → Jorge
    - `supa` → Susana
    - `juanv` → Juan

  - Si no aparece ninguno de esos tags, por defecto: Juan.

  
- Envía email con **asunto dinámico**:
  - Si hay pallets: `VENDIDO {n} pallets {material} a {Cliente}`
  - Si no hay pallets: `VENDIDO {n} uds {material} a {Cliente}`
- Mantiene **estado** de pedidos ya procesados (no reenvía).

---

## 📦 Requisitos

- Python **3.11+**
- Cuenta SMTP (si usas Gmail, **contraseña de aplicación** y `MAIL_FROM = SMTP_USER`)
- Clave de API de Holded

---

## 🧩 Instalación (local)

Instala dependencias:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```
Crea un .env (solo para ejecución local; en GitHub Actions se usan Secrets):

```dotenv
# Holded
HOLDED_API_KEY=tu_api_key
HOLDED_USE_BEARER=false   # o true si tu API usa Bearer

# SMTP
MAIL_FROM=tu_correo@dominio.com
MAIL_TO=dest1@dom.com,dest2@dom.com
SMTP_HOST=smtp.dominio.com
SMTP_PORT=587             # 587 STARTTLS | 465 SSL
SMTP_USER=tu_correo@dominio.com
SMTP_PASS=tu_contraseña_o_app_password
```
**Nota (Gmail):** usa una contraseña de aplicación y asegúrate de que `MAIL_FROM = SMTP_USER`.


## ⚡ Uso

```bash
# Consultar todo el año en curso + hoy (recomendado para detectar cancelaciones antiguas)
python so_mapper.py --ytd

# Enviar correos (VENDIDO/CANCELADO) y persistir estado en .state/so_status.json
python so_mapper.py --ytd --send-email --status-file .state/so_status.json

# Primer arranque sin correos (sembrar estado)
python so_mapper.py --ytd --status-file .state/so_status.json --quiet

# Ventana corta (últimos 30 minutos)
python so_mapper.py --minutes 30 --send-email --status-file .state/so_status.json
```

## 🤖 Automatización con GitHub Actions

El workflow ```.github/workflows/holded-mailer.yml``` ejecuta el script cada 5 minutos.
Se apoya en GitHub Secrets para las variables de entorno (`HOLDED_API_KEY`, `MAIL_FROM`, etc.).
Esto permite tener un mailer autónomo sin necesidad de servidor local.


## 📂 Estructura del proyecto

```bash
Reserva_Material/
├── .github/workflows/holded-mailer.yml   # Workflow CI/CD
├── .state/so_status.json     # Estado de pedidos procesados
├── inspect_so.py                         # Script para inspeccionar un pedido
├── so_mapper.py                          # Script principal (mailer)
├── requirements.txt                      # Dependencias
└── README.md                             # Documentación
```

## 🛠️ Tecnologías usadas
- **Python 3.11+**
- `requests` – para interactuar con la API de Holded
- `python-dotenv` – gestión de variables de entorno
- `smtplib` + `email.mime` – envío de correos
- `tzdata` – gestión de zonas horarias
- **GitHub Actions** – automatización y ejecución programada